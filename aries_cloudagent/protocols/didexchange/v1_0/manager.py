"""Classes to manage connection establishment under RFC 23 (DID exchange)."""

import json
import logging
from typing import Optional, Sequence, Union

from did_peer_4 import LONG_PATTERN, long_to_short

from ....connections.base_manager import BaseConnectionManager
from ....connections.models.conn_record import ConnRecord
from ....connections.models.connection_target import ConnectionTarget
from ....core.error import BaseError
from ....core.oob_processor import OobMessageProcessor
from ....core.profile import Profile
from ....did.did_key import DIDKey
from ....messaging.decorators.attach_decorator import AttachDecorator
from ....messaging.responder import BaseResponder
from ....storage.error import StorageNotFoundError
from ....transport.inbound.receipt import MessageReceipt
from ....wallet.base import BaseWallet
from ....wallet.did_method import SOV
from ....wallet.did_posture import DIDPosture
from ....wallet.error import WalletError
from ....wallet.key_type import ED25519
from ...coordinate_mediation.v1_0.manager import MediationManager
from ...discovery.v2_0.manager import V20DiscoveryMgr
from ...out_of_band.v1_0.messages.invitation import (
    InvitationMessage as OOBInvitationMessage,
)
from ...out_of_band.v1_0.messages.service import Service as OOBService
from .message_types import ARIES_PROTOCOL as DIDX_PROTO
from .messages.complete import DIDXComplete
from .messages.problem_report import DIDXProblemReport, ProblemReportReason
from .messages.request import DIDXRequest
from .messages.response import DIDXResponse


class DIDXManagerError(BaseError):
    """Connection error."""


class DIDXManager(BaseConnectionManager):
    """Class for managing connections under RFC 23 (DID exchange)."""

    def __init__(self, profile: Profile):
        """Initialize a DIDXManager.

        Args:
            profile: The profile for this did exchange manager
        """
        self._profile = profile
        self._logger = logging.getLogger(__name__)
        super().__init__(self._profile)

    @property
    def profile(self) -> Profile:
        """Accessor for the current profile.

        Returns:
            The profile for this did exchange manager

        """
        return self._profile

    async def receive_invitation(
        self,
        invitation: OOBInvitationMessage,
        their_public_did: Optional[str] = None,
        auto_accept: Optional[bool] = None,
        alias: Optional[str] = None,
        mediation_id: Optional[str] = None,
    ) -> ConnRecord:  # leave in didexchange as it uses a responder: not out-of-band
        """Create a new connection record to track a received invitation.

        Args:
            invitation: invitation to store
            their_public_did: their public DID
            auto_accept: set to auto-accept invitation (None to use config)
            alias: optional alias to set on record
            mediation_id: record id for mediation with routing_keys, service endpoint

        Returns:
            The new `ConnRecord` instance

        """
        if not invitation.services:
            raise DIDXManagerError(
                "Invitation must contain service blocks or service DIDs"
            )
        else:
            for s in invitation.services:
                if isinstance(s, OOBService):
                    if not s.recipient_keys or not s.service_endpoint:
                        raise DIDXManagerError(
                            "All service blocks in invitation with no service DIDs "
                            "must contain recipient key(s) and service endpoint(s)"
                        )

        accept = (
            ConnRecord.ACCEPT_AUTO
            if (
                auto_accept
                or (
                    auto_accept is None
                    and self.profile.settings.get("debug.auto_accept_invites")
                )
            )
            else ConnRecord.ACCEPT_MANUAL
        )

        service_item = invitation.services[0]
        # Create connection record
        conn_rec = ConnRecord(
            invitation_key=(
                DIDKey.from_did(service_item.recipient_keys[0]).public_key_b58
                if isinstance(service_item, OOBService)
                else None
            ),
            invitation_msg_id=invitation._id,
            their_label=invitation.label,
            their_role=ConnRecord.Role.RESPONDER.rfc23,
            state=ConnRecord.State.INVITATION.rfc160,
            accept=accept,
            alias=alias,
            their_public_did=their_public_did,
            connection_protocol=DIDX_PROTO,
        )

        async with self.profile.session() as session:
            await conn_rec.save(
                session,
                reason="Created new connection record from invitation",
                log_params={
                    "invitation": invitation,
                    "their_role": ConnRecord.Role.RESPONDER.rfc23,
                },
            )

            # Save the invitation for later processing
            await conn_rec.attach_invitation(session, invitation)
            if not conn_rec.invitation_key and conn_rec.their_public_did:
                targets = await self.resolve_connection_targets(
                    conn_rec.their_public_did
                )
                conn_rec.invitation_key = targets[0].recipient_keys[0]

        await self._route_manager.save_mediator_for_connection(
            self.profile, conn_rec, mediation_id=mediation_id
        )

        if conn_rec.accept == ConnRecord.ACCEPT_AUTO:
            request = await self.create_request(conn_rec, mediation_id=mediation_id)
            responder = self.profile.inject_or(BaseResponder)
            if responder:
                await responder.send_reply(
                    request,
                    connection_id=conn_rec.connection_id,
                )

                conn_rec.state = ConnRecord.State.REQUEST.rfc160
                async with self.profile.session() as session:
                    await conn_rec.save(session, reason="Sent connection request")
        else:
            self._logger.debug("Connection invitation will await acceptance")

        return conn_rec

    async def create_request_implicit(
        self,
        their_public_did: str,
        my_label: str = None,
        my_endpoint: str = None,
        mediation_id: str = None,
        use_public_did: bool = False,
        alias: str = None,
        goal_code: str = None,
        goal: str = None,
        auto_accept: bool = False,
    ) -> ConnRecord:
        """Create and send a request against a public DID only (no explicit invitation).

        Args:
            their_public_did: public DID to which to request a connection
            my_label: my label for request
            my_endpoint: my endpoint
            mediation_id: record id for mediation with routing_keys, service endpoint
            use_public_did: use my public DID for this connection
            goal_code: Optional self-attested code for sharing intent of connection
            goal: Optional self-attested string for sharing intent of connection
            auto_accept: auto-accept a corresponding connection request

        Returns:
            The new `ConnRecord` instance

        """
        my_public_info = None
        if use_public_did:
            async with self.profile.session() as session:
                wallet = session.inject(BaseWallet)
                my_public_info = await wallet.get_public_did()
                if not my_public_info:
                    raise WalletError("No public DID configured")
                if (
                    my_public_info.did == their_public_did
                    or f"did:sov:{my_public_info.did}" == their_public_did
                ):
                    raise DIDXManagerError(
                        "Cannot connect to yourself through public DID"
                    )
                try:
                    await ConnRecord.retrieve_by_did(
                        session,
                        their_did=their_public_did,
                        my_did=my_public_info.did,
                    )
                    raise DIDXManagerError(
                        "Connection already exists for their_did "
                        f"{their_public_did} and my_did {my_public_info.did}"
                    )
                except StorageNotFoundError:
                    pass
        auto_accept = bool(
            auto_accept
            or (
                auto_accept is None
                and self.profile.settings.get("debug.auto_accept_requests")
            )
        )
        conn_rec = ConnRecord(
            my_did=my_public_info.did
            if my_public_info
            else None,  # create-request will fill in on local DID creation
            their_did=their_public_did,
            their_label=None,
            their_role=ConnRecord.Role.RESPONDER.rfc23,
            invitation_key=None,
            invitation_msg_id=None,
            alias=alias,
            their_public_did=their_public_did,
            connection_protocol=DIDX_PROTO,
            accept=ConnRecord.ACCEPT_AUTO if auto_accept else ConnRecord.ACCEPT_MANUAL,
        )
        request = await self.create_request(  # saves and updates conn_rec
            conn_rec=conn_rec,
            my_label=my_label,
            my_endpoint=my_endpoint,
            mediation_id=mediation_id,
            goal_code=goal_code,
            goal=goal,
            use_public_did=bool(my_public_info),
        )
        conn_rec.request_id = request._id
        conn_rec.state = ConnRecord.State.REQUEST.rfc160
        async with self.profile.session() as session:
            await conn_rec.save(session, reason="Created connection request")
        responder = self.profile.inject_or(BaseResponder)
        if responder:
            await responder.send(request, connection_id=conn_rec.connection_id)

        return conn_rec

    async def create_request(
        self,
        conn_rec: ConnRecord,
        my_label: Optional[str] = None,
        my_endpoint: Optional[str] = None,
        mediation_id: Optional[str] = None,
        goal_code: Optional[str] = None,
        goal: Optional[str] = None,
        use_public_did: bool = False,
    ) -> DIDXRequest:
        """Create a new connection request for a previously-received invitation.

        Args:
            conn_rec: The `ConnRecord` representing the invitation to accept
            my_label: My label for request
            my_endpoint: My endpoint
            mediation_id: The record id for mediation that contains routing_keys and
                service endpoint
            goal_code: Optional self-attested code for sharing intent of connection
            goal: Optional self-attested string for sharing intent of connection
            use_public_did: Flag whether to use public DID and omit DID Doc
                attachment on request
        Returns:
            A new `DIDXRequest` message to send to the other agent

        """
        # Mediation Support
        mediation_records = await self._route_manager.mediation_records_for_connection(
            self.profile,
            conn_rec,
            mediation_id,
            or_default=True,
        )

        my_info = None

        # Create connection request message
        if my_endpoint:
            my_endpoints = [my_endpoint]
        else:
            my_endpoints = []
            default_endpoint = self.profile.settings.get("default_endpoint")
            if default_endpoint:
                my_endpoints.append(default_endpoint)
            my_endpoints.extend(self.profile.settings.get("additional_endpoints", []))

        emit_did_peer_4 = self.profile.settings.get("emit_did_peer_4")
        emit_did_peer_2 = self.profile.settings.get("emit_did_peer_2")
        if emit_did_peer_2 and emit_did_peer_4:
            self._logger.warning(
                "emit_did_peer_2 and emit_did_peer_4 both set, \
                 using did:peer:4"
            )

        if conn_rec.my_did:
            async with self.profile.session() as session:
                wallet = session.inject(BaseWallet)
                my_info = await wallet.get_local_did(conn_rec.my_did)
        elif emit_did_peer_4:
            my_info = await self.create_did_peer_4(my_endpoints, mediation_records)
            conn_rec.my_did = my_info.did
        elif emit_did_peer_2:
            my_info = await self.create_did_peer_2(my_endpoints, mediation_records)
            conn_rec.my_did = my_info.did
        else:
            # Create new DID for connection
            async with self.profile.session() as session:
                wallet = session.inject(BaseWallet)
                my_info = await wallet.create_local_did(
                    method=SOV,
                    key_type=ED25519,
                )
                conn_rec.my_did = my_info.did

        if use_public_did or emit_did_peer_2 or emit_did_peer_4:
            # Omit DID Doc attachment if we're using a public DID
            did_doc = None
            attach = None
            did = conn_rec.my_did
            if not did.startswith("did:"):
                did = f"did:sov:{did}"
        else:
            did_doc = await self.create_did_document(
                my_info,
                my_endpoints,
                mediation_records=mediation_records,
            )
            attach = AttachDecorator.data_base64(did_doc.serialize())
            async with self.profile.session() as session:
                wallet = session.inject(BaseWallet)
                await attach.data.sign(my_info.verkey, wallet)
            did = conn_rec.my_did

        did_url = None
        if conn_rec.their_public_did is not None:
            services = await self.resolve_didcomm_services(conn_rec.their_public_did)
            if services:
                did_url = services[0].id

        pthid = conn_rec.invitation_msg_id or did_url

        if not my_label:
            my_label = self.profile.settings.get("default_label")

        request = DIDXRequest(
            label=my_label,
            did=did,
            did_doc_attach=attach,
            goal_code=goal_code,
            goal=goal,
        )
        request.assign_thread_id(thid=request._id, pthid=pthid)

        # Update connection state
        conn_rec.request_id = request._id
        conn_rec.state = ConnRecord.State.REQUEST.rfc160
        async with self.profile.session() as session:
            await conn_rec.save(session, reason="Created connection request")

        # Idempotent; if routing has already been set up, no action taken
        await self._route_manager.route_connection_as_invitee(
            self.profile, conn_rec, mediation_records
        )

        return request

    async def receive_request(
        self,
        request: DIDXRequest,
        recipient_did: str,
        recipient_verkey: Optional[str] = None,
        my_endpoint: Optional[str] = None,
        alias: Optional[str] = None,
        auto_accept_implicit: Optional[bool] = None,
    ) -> ConnRecord:
        """Receive and store a connection request.

        Args:
            request: The `DIDXRequest` to accept
            recipient_did: The (unqualified) recipient DID
            recipient_verkey: The recipient verkey: None for public recipient DID
            my_endpoint: My endpoint
            alias: Alias for the connection
            auto_accept: Auto-accept request against implicit invitation
        Returns:
            The new or updated `ConnRecord` instance

        """
        ConnRecord.log_state(
            "Receiving connection request",
            {"request": request},
            settings=self.profile.settings,
        )

        conn_rec = None
        connection_key = None
        my_info = None

        # Determine what key will need to sign the response
        if recipient_verkey:  # peer DID
            connection_key = recipient_verkey
            try:
                async with self.profile.session() as session:
                    conn_rec = await ConnRecord.retrieve_by_invitation_key(
                        session=session,
                        invitation_key=connection_key,
                        their_role=ConnRecord.Role.REQUESTER.rfc23,
                    )
            except StorageNotFoundError:
                if recipient_verkey:
                    raise DIDXManagerError(
                        "No explicit invitation found for pairwise connection "
                        f"in state {ConnRecord.State.INVITATION.rfc23}: "
                        "a prior connection request may have updated the connection state"
                    )
        else:
            if not self.profile.settings.get("public_invites"):
                raise DIDXManagerError(
                    "Public invitations are not enabled: connection request refused"
                )

            async with self.profile.session() as session:
                wallet = session.inject(BaseWallet)
                my_info = await wallet.get_local_did(recipient_did)
            if DIDPosture.get(my_info.metadata) not in (
                DIDPosture.PUBLIC,
                DIDPosture.POSTED,
            ):
                raise DIDXManagerError(f"Request DID {recipient_did} is not public")
            connection_key = my_info.verkey

            async with self.profile.session() as session:
                conn_rec = await ConnRecord.retrieve_by_invitation_msg_id(
                    session=session,
                    invitation_msg_id=request._thread.pthid,
                    their_role=ConnRecord.Role.REQUESTER.rfc23,
                )

        if conn_rec:  # invitation was explicit
            connection_key = conn_rec.invitation_key
            if conn_rec.is_multiuse_invitation:
                async with self.profile.session() as session:
                    wallet = session.inject(BaseWallet)
                    my_info = await wallet.create_local_did(
                        method=SOV,
                        key_type=ED25519,
                    )

                new_conn_rec = ConnRecord(
                    invitation_key=connection_key,
                    my_did=my_info.did,
                    state=ConnRecord.State.REQUEST.rfc160,
                    accept=conn_rec.accept,
                    their_role=conn_rec.their_role,
                    connection_protocol=DIDX_PROTO,
                )
                async with self.profile.session() as session:
                    await new_conn_rec.save(
                        session,
                        reason=(
                            "Received connection request from multi-use invitation DID"
                        ),
                    )

                # Transfer metadata from multi-use to new connection
                # Must come after save so there's an ID to associate with metadata
                async with self.profile.session() as session:
                    for key, value in (
                        await conn_rec.metadata_get_all(session)
                    ).items():
                        await new_conn_rec.metadata_set(session, key, value)

                conn_rec = new_conn_rec

        # request DID doc describes requester DID
        if request.did_doc_attach and request.did_doc_attach.data:
            self._logger.debug("Received DID Doc attachment in request")
            async with self.profile.session() as session:
                wallet = session.inject(BaseWallet)
                conn_did_doc = await self.verify_diddoc(wallet, request.did_doc_attach)
                await self.store_did_document(conn_did_doc)

            # Special case: legacy DIDs were unqualified in request, qualified in doc
            if request.did and not request.did.startswith("did:"):
                did_to_check = f"did:sov:{request.did}"
            else:
                did_to_check = request.did

            if did_to_check != conn_did_doc["id"]:
                raise DIDXManagerError(
                    (
                        f"Connection DID {request.did} does not match "
                        f"DID Doc id {conn_did_doc['id']}"
                    ),
                    error_code=ProblemReportReason.REQUEST_NOT_ACCEPTED.value,
                )
        else:
            if request.did is None:
                raise DIDXManagerError("No DID in request")

            self._logger.debug(
                "No DID Doc attachment in request; doc will be resolved from DID"
            )
            await self.record_did(request.did)

        if conn_rec:  # request is against explicit invitation
            auto_accept = (
                conn_rec.accept == ConnRecord.ACCEPT_AUTO
            )  # null=manual; oob-manager calculated at conn rec creation

            conn_rec.their_label = request.label
            if alias:
                conn_rec.alias = alias
            conn_rec.their_did = request.did
            conn_rec.state = ConnRecord.State.REQUEST.rfc160
            conn_rec.request_id = request._id
            async with self.profile.session() as session:
                await conn_rec.save(
                    session, reason="Received connection request from invitation"
                )
        else:
            # request is against implicit invitation on public DID
            if not self.profile.settings.get("requests_through_public_did"):
                raise DIDXManagerError(
                    "Unsolicited connection requests to public DID is not enabled"
                )

            auto_accept = bool(
                auto_accept_implicit
                or (
                    auto_accept_implicit is None
                    and self.profile.settings.get("debug.auto_accept_requests", False)
                )
            )

            conn_rec = ConnRecord(
                my_did=None,  # Defer DID creation until create_response
                accept=(
                    ConnRecord.ACCEPT_AUTO if auto_accept else ConnRecord.ACCEPT_MANUAL
                ),
                their_did=request.did,
                their_label=request.label,
                alias=alias,
                their_role=ConnRecord.Role.REQUESTER.rfc23,
                invitation_key=connection_key,
                invitation_msg_id=None,
                request_id=request._id,
                state=ConnRecord.State.REQUEST.rfc160,
                connection_protocol=DIDX_PROTO,
            )
            async with self.profile.session() as session:
                await conn_rec.save(
                    session, reason="Received connection request from public DID"
                )

        async with self.profile.session() as session:
            # Attach the connection request so it can be found and responded to
            await conn_rec.attach_request(session, request)

        # Clean associated oob record if not needed anymore
        oob_processor = self.profile.inject(OobMessageProcessor)
        await oob_processor.clean_finished_oob_record(self.profile, request)

        return conn_rec

    async def create_response(
        self,
        conn_rec: ConnRecord,
        my_endpoint: Optional[str] = None,
        mediation_id: Optional[str] = None,
        use_public_did: Optional[bool] = None,
    ) -> DIDXResponse:
        """Create a connection response for a received connection request.

        Args:
            conn_rec: The `ConnRecord` with a pending connection request
            my_endpoint: Current agent endpoint
            mediation_id: The record id for mediation that contains routing_keys and
                service endpoint

        Returns:
            New `DIDXResponse` message

        """
        ConnRecord.log_state(
            "Creating connection response",
            {"connection_id": conn_rec.connection_id},
            settings=self.profile.settings,
        )

        mediation_records = await self._route_manager.mediation_records_for_connection(
            self.profile, conn_rec, mediation_id
        )

        if ConnRecord.State.get(conn_rec.state) is not ConnRecord.State.REQUEST:
            raise DIDXManagerError(
                f"Connection not in state {ConnRecord.State.REQUEST.rfc23}"
            )
        async with self.profile.session() as session:
            request = await conn_rec.retrieve_request(session)

        if my_endpoint:
            my_endpoints = [my_endpoint]
        else:
            my_endpoints = []
            default_endpoint = self.profile.settings.get("default_endpoint")
            if default_endpoint:
                my_endpoints.append(default_endpoint)
            my_endpoints.extend(self.profile.settings.get("additional_endpoints", []))

        respond_with_did_peer_2 = self.profile.settings.get("emit_did_peer_2") or (
            conn_rec.their_did and conn_rec.their_did.startswith("did:peer:2")
        )
        respond_with_did_peer_4 = self.profile.settings.get("emit_did_peer_4") or (
            conn_rec.their_did and conn_rec.their_did.startswith("did:peer:4")
        )

        if conn_rec.my_did:
            async with self.profile.session() as session:
                wallet = session.inject(BaseWallet)
                my_info = await wallet.get_local_did(conn_rec.my_did)
            did = my_info.did
        elif respond_with_did_peer_4:
            my_info = await self.create_did_peer_4(my_endpoints, mediation_records)
            conn_rec.my_did = my_info.did
            did = my_info.did
        elif respond_with_did_peer_2:
            my_info = await self.create_did_peer_2(my_endpoints, mediation_records)
            conn_rec.my_did = my_info.did
            did = my_info.did
        elif use_public_did:
            async with self.profile.session() as session:
                wallet = session.inject(BaseWallet)
                my_info = await wallet.get_public_did()
            if not my_info:
                raise DIDXManagerError("No public DID configured")
            conn_rec.my_did = my_info.did
            did = my_info.did
            if not did.startswith("did:"):
                did = f"did:sov:{did}"

        else:
            async with self.profile.session() as session:
                wallet = session.inject(BaseWallet)
                my_info = await wallet.create_local_did(
                    method=SOV,
                    key_type=ED25519,
                )
            conn_rec.my_did = my_info.did
            did = my_info.did

        # Idempotent; if routing has already been set up, no action taken
        await self._route_manager.route_connection_as_inviter(
            self.profile, conn_rec, mediation_records
        )

        if use_public_did or respond_with_did_peer_2 or respond_with_did_peer_4:
            # Omit DID Doc attachment if we're using a public DID or peer did
            attach = AttachDecorator.data_base64_string(did)
            async with self.profile.session() as session:
                wallet = session.inject(BaseWallet)
                if conn_rec.invitation_key is not None:
                    await attach.data.sign(conn_rec.invitation_key, wallet)
                else:
                    self._logger.warning("Invitation key was not set for connection")
                    attach = None
            response = DIDXResponse(did=did, did_rotate_attach=attach)
        else:
            did_doc = await self.create_did_document(
                my_info,
                my_endpoints,
                mediation_records=mediation_records,
            )
            attach = AttachDecorator.data_base64(did_doc.serialize())
            async with self.profile.session() as session:
                wallet = session.inject(BaseWallet)
                await attach.data.sign(conn_rec.invitation_key, wallet)
            response = DIDXResponse(did=did, did_doc_attach=attach)

        # Assign thread information
        response.assign_thread_from(request)
        response.assign_trace_from(request)

        # Update connection state
        conn_rec.state = ConnRecord.State.RESPONSE.rfc23
        async with self.profile.session() as session:
            await conn_rec.save(
                session,
                reason="Created connection response",
                log_params={"response": response},
            )

        async with self.profile.session() as session:
            send_mediation_request = await conn_rec.metadata_get(
                session, MediationManager.SEND_REQ_AFTER_CONNECTION
            )
        if send_mediation_request:
            temp_mediation_mgr = MediationManager(self.profile)
            _record, request = await temp_mediation_mgr.prepare_request(
                conn_rec.connection_id
            )
            responder = self.profile.inject(BaseResponder)
            await responder.send(request, connection_id=conn_rec.connection_id)

        return response

    async def accept_response(
        self,
        response: DIDXResponse,
        receipt: MessageReceipt,
    ) -> ConnRecord:
        """Accept a connection response under RFC 23 (DID exchange).

        Process a `DIDXResponse` message by looking up
        the connection request and setting up the pairwise connection.

        Args:
            response: The `DIDXResponse` to accept
            receipt: The message receipt

        Returns:
            The updated `ConnRecord` representing the connection

        Raises:
            DIDXManagerError: If there is no DID associated with the
                connection response
            DIDXManagerError: If the corresponding connection is not
                in the request-sent state

        """

        conn_rec = None
        if response._thread:
            # identify the request by the thread ID
            async with self.profile.session() as session:
                try:
                    conn_rec = await ConnRecord.retrieve_by_request_id(
                        session,
                        response._thread_id,
                        their_role=ConnRecord.Role.RESPONDER.rfc23,
                    )
                except StorageNotFoundError:
                    pass
                if not conn_rec:
                    try:
                        conn_rec = await ConnRecord.retrieve_by_request_id(
                            session,
                            response._thread_id,
                            their_role=ConnRecord.Role.RESPONDER.rfc160,
                        )
                    except StorageNotFoundError:
                        pass

        if not conn_rec and receipt.sender_did:
            # identify connection by the DID they used for us
            try:
                async with self.profile.session() as session:
                    conn_rec = await ConnRecord.retrieve_by_did(
                        session=session,
                        their_did=receipt.sender_did,
                        my_did=receipt.recipient_did,
                        their_role=ConnRecord.Role.RESPONDER.rfc23,
                    )
            except StorageNotFoundError:
                pass

        if not conn_rec:
            raise DIDXManagerError(
                "No corresponding connection request found",
                error_code=ProblemReportReason.RESPONSE_NOT_ACCEPTED.value,
            )

        if ConnRecord.State.get(conn_rec.state) is not ConnRecord.State.REQUEST:
            raise DIDXManagerError(
                "Cannot accept connection response for connection"
                f" in state: {conn_rec.state}"
            )

        their_did = response.did
        if response.did_doc_attach:
            async with self.profile.session() as session:
                wallet = session.inject(BaseWallet)
                conn_did_doc = await self.verify_diddoc(
                    wallet, response.did_doc_attach, conn_rec.invitation_key
                )
            # Special case: legacy DIDs were unqualified in response, qualified in doc
            if their_did and not their_did.startswith("did:"):
                did_to_check = f"did:sov:{their_did}"
            else:
                did_to_check = their_did

            if did_to_check != conn_did_doc["id"]:
                raise DIDXManagerError(
                    f"Connection DID {their_did} "
                    f"does not match DID doc id {conn_did_doc['id']}"
                )
            await self.store_did_document(conn_did_doc)
        else:
            if response.did is None:
                raise DIDXManagerError("No DID in response")

            if response.did_rotate_attach is None:
                raise DIDXManagerError(
                    "did_rotate~attach required if no signed doc attachment"
                )

            self._logger.debug("did_rotate~attach found; verifying signature")
            async with self.profile.session() as session:
                wallet = session.inject(BaseWallet)
                signed_did = await self.verify_rotate(
                    wallet, response.did_rotate_attach, conn_rec.invitation_key
                )
                if their_did != response.did:
                    raise DIDXManagerError(
                        f"Connection DID {their_did} "
                        f"does not match singed DID rotate {signed_did}"
                    )

            self._logger.debug(
                "No DID Doc attachment in response; doc will be resolved from DID"
            )
            await self.record_did(response.did)

        conn_rec.their_did = their_did

        # The long format I sent has been acknoledged, use short form now.
        if LONG_PATTERN.match(conn_rec.my_did or ""):
            conn_rec.my_did = await self.long_did_peer_4_to_short(conn_rec.my_did)
        if LONG_PATTERN.match(conn_rec.their_did or ""):
            conn_rec.their_did = long_to_short(conn_rec.their_did)

        conn_rec.state = ConnRecord.State.RESPONSE.rfc160
        async with self.profile.session() as session:
            await conn_rec.save(session, reason="Accepted connection response")

        async with self.profile.session() as session:
            send_mediation_request = await conn_rec.metadata_get(
                session, MediationManager.SEND_REQ_AFTER_CONNECTION
            )
        if send_mediation_request:
            temp_mediation_mgr = MediationManager(self.profile)
            _record, request = await temp_mediation_mgr.prepare_request(
                conn_rec.connection_id
            )
            responder = self.profile.inject(BaseResponder)
            await responder.send(request, connection_id=conn_rec.connection_id)

        # create and send connection-complete message
        complete = DIDXComplete()
        complete.assign_thread_from(response)
        responder = self.profile.inject_or(BaseResponder)
        if responder:
            await responder.send_reply(complete, connection_id=conn_rec.connection_id)

            conn_rec.state = ConnRecord.State.COMPLETED.rfc160
            async with self.profile.session() as session:
                await conn_rec.save(session, reason="Sent connection complete")
                if session.settings.get("auto_disclose_features"):
                    discovery_mgr = V20DiscoveryMgr(self._profile)
                    await discovery_mgr.proactive_disclose_features(
                        connection_id=conn_rec.connection_id
                    )

        return conn_rec

    async def accept_complete(
        self,
        complete: DIDXComplete,
        receipt: MessageReceipt,
    ) -> ConnRecord:
        """Accept a connection complete message under RFC 23 (DID exchange).

        Process a `DIDXComplete` message by looking up
        the connection record and marking the exchange complete.

        Args:
            complete: The `DIDXComplete` to accept
            receipt: The message receipt

        Returns:
            The updated `ConnRecord` representing the connection

        Raises:
            DIDXManagerError: If the corresponding connection does not exist
                or is not in the response-sent state

        """
        conn_rec = None

        # identify the request by the thread ID
        async with self.profile.session() as session:
            try:
                conn_rec = await ConnRecord.retrieve_by_request_id(
                    session,
                    complete._thread_id,
                    their_role=ConnRecord.Role.REQUESTER.rfc23,
                )
            except StorageNotFoundError:
                pass

            if not conn_rec:
                try:
                    conn_rec = await ConnRecord.retrieve_by_request_id(
                        session,
                        complete._thread_id,
                        their_role=ConnRecord.Role.REQUESTER.rfc160,
                    )
                except StorageNotFoundError:
                    pass

        if not conn_rec:
            raise DIDXManagerError(
                "No corresponding connection request found",
                error_code=ProblemReportReason.COMPLETE_NOT_ACCEPTED.value,
            )

        if LONG_PATTERN.match(conn_rec.my_did or ""):
            conn_rec.my_did = await self.long_did_peer_4_to_short(conn_rec.my_did)
        if LONG_PATTERN.match(conn_rec.their_did or ""):
            conn_rec.their_did = long_to_short(conn_rec.their_did)

        conn_rec.state = ConnRecord.State.COMPLETED.rfc160
        async with self.profile.session() as session:
            await conn_rec.save(session, reason="Received connection complete")
            if session.settings.get("auto_disclose_features"):
                discovery_mgr = V20DiscoveryMgr(self._profile)
                await discovery_mgr.proactive_disclose_features(
                    connection_id=conn_rec.connection_id
                )

        return conn_rec

    async def reject(
        self,
        conn_rec: ConnRecord,
        *,
        reason: Optional[str] = None,
    ) -> DIDXProblemReport:
        """Abandon an existing DID exchange."""
        state_to_reject_code = {
            ConnRecord.State.INVITATION.rfc23
            + "-received": ProblemReportReason.INVITATION_NOT_ACCEPTED,
            ConnRecord.State.REQUEST.rfc23
            + "-received": ProblemReportReason.REQUEST_NOT_ACCEPTED,
        }
        code = state_to_reject_code.get(conn_rec.rfc23_state)
        if not code:
            raise DIDXManagerError(
                f"Cannot reject connection in state: {conn_rec.rfc23_state}"
            )

        async with self.profile.session() as session:
            await conn_rec.abandon(session, reason=reason)

        report = DIDXProblemReport(
            description={
                "code": code.value,
                "en": reason or "DID exchange rejected",
            },
        )

        # TODO Delete the record?
        return report

    async def receive_problem_report(
        self,
        conn_rec: ConnRecord,
        report: DIDXProblemReport,
    ):
        """Receive problem report."""
        if not report.description:
            raise DIDXManagerError("Missing description in problem report")

        if report.description.get("code") in {
            reason.value for reason in ProblemReportReason
        }:
            self._logger.info("Problem report indicates connection is abandoned")
            async with self.profile.session() as session:
                await conn_rec.abandon(
                    session,
                    reason=report.description.get("en"),
                )
        else:
            raise DIDXManagerError(
                f"Received unrecognized problem report: {report.description}"
            )

    async def verify_diddoc(
        self,
        wallet: BaseWallet,
        attached: AttachDecorator,
        invi_key: str = None,
    ) -> dict:
        """Verify DIDDoc attachment and return signed data."""
        signed_diddoc_bytes = attached.data.signed
        if not signed_diddoc_bytes:
            raise DIDXManagerError("DID doc attachment is not signed.")
        if not await attached.data.verify(wallet, invi_key):
            raise DIDXManagerError("DID doc attachment signature failed verification")

        return json.loads(signed_diddoc_bytes.decode())

    async def verify_rotate(
        self,
        wallet: BaseWallet,
        attached: AttachDecorator,
        invi_key: str = None,
    ) -> str:
        """Verify a signed DID rotate attachment and return did."""
        signed_diddoc_bytes = attached.data.signed
        if not signed_diddoc_bytes:
            raise DIDXManagerError("DID rotate attachment is not signed.")
        if not await attached.data.verify(wallet, invi_key):
            raise DIDXManagerError(
                "DID rotate attachment signature failed verification"
            )

        return signed_diddoc_bytes.decode()

    async def manager_error_to_problem_report(
        self,
        e: DIDXManagerError,
        message: Union[DIDXRequest, DIDXResponse],
        message_receipt,
    ) -> tuple[DIDXProblemReport, Sequence[ConnectionTarget]]:
        """Convert DIDXManagerError to problem report."""
        self._logger.exception("Error receiving RFC 23 connection request")
        targets = None
        report = None
        if e.error_code:
            report = DIDXProblemReport(
                description={"en": e.message, "code": e.error_code}
            )
            report.assign_thread_from(message)
            if message.did_doc_attach:
                try:
                    # convert diddoc attachment to diddoc...
                    async with self.profile.session() as session:
                        wallet = session.inject(BaseWallet)
                        conn_did_doc = await self.verify_diddoc(
                            wallet, message.did_doc_attach
                        )
                    # get the connection targets...
                    targets = self.diddoc_connection_targets(
                        conn_did_doc,
                        message_receipt.recipient_verkey,
                    )
                except DIDXManagerError:
                    self._logger.exception("Error parsing DIDDoc for problem report")

        return report, targets
