@RFC0453
Feature: RFC 0453 Aries agent issue credential

  @T003-RFC0453
  Scenario Outline: Issue a credential with the Issuer beginning with an offer
    Given we have "2" agents
      | name  | role    | capabilities        |
      | Acme  | issuer  | <Acme_capabilities> |
      | Bob   | holder  | <Bob_capabilities>  |
    And "Acme" and "Bob" have an existing connection
    And "Acme" is ready to issue a credential for <Schema_name>
    When "Acme" offers a credential with data <Credential_data>
    Then "Bob" has the credential issued

    @GHA @WalletType_Askar
    Examples:
       | Acme_capabilities                      | Bob_capabilities          | Schema_name    | Credential_data          |
       | --public-did                           |                           | driverslicense | Data_DL_NormalizedValues |
       | --public-did --did-exchange            | --did-exchange            | driverslicense | Data_DL_NormalizedValues |
       | --public-did --mediation               | --mediation               | driverslicense | Data_DL_NormalizedValues |
       | --public-did --multitenant             | --multitenant --log-file  | driverslicense | Data_DL_NormalizedValues |

    @GHA @WalletType_Askar_AnonCreds
    Examples:
       | Acme_capabilities                      | Bob_capabilities          | Schema_name    | Credential_data          |
       | --public-did --wallet-type askar-anoncreds | --wallet-type askar-anoncreds | driverslicense | Data_DL_NormalizedValues |
       | --public-did --wallet-type askar-anoncreds |                               | driverslicense | Data_DL_NormalizedValues |
       | --public-did                           | --wallet-type askar-anoncreds | driverslicense | Data_DL_NormalizedValues |

  @T003-RFC0453
  Scenario Outline: Holder accepts a deleted credential offer
    Given we have "2" agents
      | name  | role    | capabilities        |
      | Acme  | issuer  | <Acme_capabilities> |
      | Bob   | holder  | <Bob_capabilities>  |
    And "Acme" and "Bob" have an existing connection
    And "Acme" is ready to issue a credential for <Schema_name>
    And "Acme" offers and deletes a credential with data <Credential_data>
    Then "Bob" has the exchange abandoned

    @GHA @WalletType_Askar
    Examples:
       | Acme_capabilities                      | Bob_capabilities          | Schema_name    | Credential_data          |
       | --public-did                           |                           | driverslicense | Data_DL_NormalizedValues |

    @WalletType_Askar_AnonCreds
    Examples:
       | Acme_capabilities                      | Bob_capabilities          | Schema_name    | Credential_data          |
       | --public-did --wallet-type askar-anoncreds | --wallet-type askar-anoncreds | driverslicense | Data_DL_NormalizedValues |

    @WalletType_Askar
    Examples:
       | Acme_capabilities                      | Bob_capabilities          | Schema_name    | Credential_data          |
       | --public-did --did-exchange            | --did-exchange            | driverslicense | Data_DL_NormalizedValues |
       | --public-did --mediation               | --mediation               | driverslicense | Data_DL_NormalizedValues |
       | --public-did --multitenant             | --multitenant             | driverslicense | Data_DL_NormalizedValues |

  @T003-RFC0453
  Scenario Outline: Issue a credential with the holder sending a request
    Given we have "2" agents
      | name  | role    | capabilities        |
      | Acme  | issuer  | <Acme_capabilities> |
      | Bob   | holder  | <Bob_capabilities>  |
    And "Acme" and "Bob" have an existing connection
    And "Acme" is ready to issue a credential for <Schema_name>
    When "Bob" requests a credential with data <Credential_data> from "Acme" it fails

    @GHA @WalletType_Askar
    Examples:
       | Acme_capabilities                      | Bob_capabilities          | Schema_name    | Credential_data          |
       | --public-did                           |                           | driverslicense | Data_DL_NormalizedValues |

    @WalletType_Askar_AnonCreds
    Examples:
       | Acme_capabilities                      | Bob_capabilities          | Schema_name    | Credential_data          |
       | --public-did --wallet-type askar-anoncreds | --wallet-type askar-anoncreds | driverslicense | Data_DL_NormalizedValues |

    @WalletType_Askar
    Examples:
       | Acme_capabilities                      | Bob_capabilities          | Schema_name    | Credential_data          |
       | --public-did --did-exchange            | --did-exchange            | driverslicense | Data_DL_NormalizedValues |
       | --public-did --mediation               | --mediation               | driverslicense | Data_DL_NormalizedValues |
       | --public-did --multitenant             | --multitenant             | driverslicense | Data_DL_NormalizedValues |


  @T003.1-RFC0453
  Scenario Outline: Holder accepts a deleted json-ld credential offer
    Given we have "2" agents
      | name  | role    | capabilities        |
      | Acme  | issuer  | <Acme_capabilities> |
      | Bob   | holder  | <Bob_capabilities>  |
    And "Acme" and "Bob" have an existing connection
    And "Acme" is ready to issue a json-ld credential for <Schema_name>
    And "Bob" is ready to receive a json-ld credential
    When "Acme" offers and deletes "Bob" a json-ld credential with data <Credential_data>
    Then "Bob" has the json-ld credential issued
    And "Acme" has the exchange completed

    @GHA @WalletType_Askar
    Examples:
       | Acme_capabilities                                   | Bob_capabilities          | Schema_name    | Credential_data          |
       | --public-did --cred-type json-ld                    |                           | driverslicense | Data_DL_NormalizedValues |

    @WalletType_Askar_AnonCreds
    Examples:
       | Acme_capabilities                                   | Bob_capabilities          | Schema_name    | Credential_data          |
       | --public-did --cred-type json-ld --wallet-type askar-anoncreds | --wallet-type askar-anoncreds | driverslicense | Data_DL_NormalizedValues |

    @WalletType_Askar
    Examples:
       | Acme_capabilities                                   | Bob_capabilities          | Schema_name    | Credential_data          |
       | --public-did --cred-type json-ld --did-exchange     | --did-exchange            | driverslicense | Data_DL_NormalizedValues |
       | --public-did --cred-type json-ld --mediation        | --mediation               | driverslicense | Data_DL_NormalizedValues |
       | --public-did --cred-type json-ld --multitenant      | --multitenant             | driverslicense | Data_DL_NormalizedValues |

  @T003.1-RFC0453
  Scenario Outline: Issue a json-ld credential with the Issuer beginning with an offer
    Given we have "2" agents
      | name  | role    | capabilities        |
      | Acme  | issuer  | <Acme_capabilities> |
      | Bob   | holder  | <Bob_capabilities>  |
    And "Acme" and "Bob" have an existing connection
    And "Acme" is ready to issue a json-ld credential for <Schema_name>
    And "Bob" is ready to receive a json-ld credential
    When "Acme" offers "Bob" a json-ld credential with data <Credential_data>
    Then "Bob" has the json-ld credential issued

    @GHA @WalletType_Askar
    Examples:
       | Acme_capabilities                                         | Bob_capabilities          | Schema_name    | Credential_data          |
       | --public-did --cred-type json-ld                          |                           | driverslicense | Data_DL_NormalizedValues |
       | --public-did --cred-type json-ld --did-exchange           | --did-exchange            | driverslicense | Data_DL_NormalizedValues |
       | --public-did --cred-type json-ld --mediation              | --mediation               | driverslicense | Data_DL_NormalizedValues |
       | --public-did --cred-type json-ld --multitenant --log-file | --multitenant             | driverslicense | Data_DL_NormalizedValues |

    @GHA @WalletType_Askar_AnonCreds
    Examples:
       | Acme_capabilities                                   | Bob_capabilities          | Schema_name    | Credential_data          |
       | --public-did --cred-type json-ld --wallet-type askar-anoncreds | --wallet-type askar-anoncreds | driverslicense | Data_DL_NormalizedValues |
       | --public-did --cred-type json-ld --did-exchange --wallet-type askar-anoncreds | --did-exchange --wallet-type askar-anoncreds | driverslicense | Data_DL_NormalizedValues |

    @WalletType_Askar_AnonCreds
    Examples:
       | Acme_capabilities                                   | Bob_capabilities          | Schema_name    | Credential_data          |
       | --public-did --cred-type json-ld --mediation --wallet-type askar-anoncreds | --mediation --wallet-type askar-anoncreds | driverslicense | Data_DL_NormalizedValues |
       | --public-did --cred-type json-ld --multitenant --wallet-type askar-anoncreds | --multitenant --wallet-type askar-anoncreds | driverslicense | Data_DL_NormalizedValues |


  @T003.1-RFC0453
  Scenario Outline: Issue a json-ld credential with the holder beginning with a request
    Given we have "2" agents
      | name  | role    | capabilities        |
      | Acme  | issuer  | <Acme_capabilities> |
      | Bob   | holder  | <Bob_capabilities>  |
    And "Acme" and "Bob" have an existing connection
    And "Acme" is ready to issue a json-ld credential for <Schema_name>
    And "Bob" is ready to receive a json-ld credential
    When "Bob" requests a json-ld credential with data <Credential_data> from "Acme"
    Then "Bob" has the json-ld credential issued

    @GHA @WalletType_Askar
    Examples:
       | Acme_capabilities                                   | Bob_capabilities          | Schema_name    | Credential_data          |
       | --public-did --cred-type json-ld                    |                           | driverslicense | Data_DL_NormalizedValues |
       | --public-did --cred-type json-ld --did-exchange     | --did-exchange            | driverslicense | Data_DL_NormalizedValues |
       | --public-did --cred-type json-ld --mediation        | --mediation               | driverslicense | Data_DL_NormalizedValues |
       | --public-did --cred-type json-ld --multitenant      | --multitenant             | driverslicense | Data_DL_NormalizedValues |

    @GHA @WalletType_Askar_AnonCreds
    Examples:
       | Acme_capabilities                                   | Bob_capabilities          | Schema_name    | Credential_data          |
       | --public-did --cred-type json-ld --wallet-type askar-anoncreds | --wallet-type askar-anoncreds | driverslicense | Data_DL_NormalizedValues |


  @T004-RFC0453
  Scenario Outline: Issue a credential with revocation, with the Issuer beginning with an offer, and then revoking the credential
    Given we have "2" agents
      | name  | role    | capabilities        |
      | Acme  | issuer  | <Acme_capabilities> |
      | Bob   | holder  | <Bob_capabilities>  |
    And "Acme" and "Bob" have an existing connection
    And "Bob" has an issued <Schema_name> credential <Credential_data> from "Acme"
    Then "Acme" revokes the credential
    And "Bob" has the credential issued

    @GHA @WalletType_Askar
    Examples:
       | Acme_capabilities                        | Bob_capabilities  | Schema_name    | Credential_data          |
       | --revocation --public-did                |                   | driverslicense | Data_DL_NormalizedValues |
       | --revocation --public-did --did-exchange | --did-exchange    | driverslicense | Data_DL_NormalizedValues |
       | --revocation --public-did --multitenant  | --multitenant     | driverslicense | Data_DL_NormalizedValues |

    @WalletType_Askar_AnonCreds
    Examples:
       | Acme_capabilities                        | Bob_capabilities  | Schema_name    | Credential_data          |
       | --revocation --public-did --wallet-type askar-anoncreds | --wallet-type askar-anoncreds | driverslicense | Data_DL_NormalizedValues |

  @T004.1-RFC0453
  Scenario Outline: Issue a credential with revocation, with the Issuer beginning with an offer, and then revoking the credential
    Given we have "2" agents
      | name  | role    | capabilities        |
      | Acme  | issuer  | <Acme_capabilities> |
      | Bob   | holder  | <Bob_capabilities>  |
    And "Acme" and "Bob" have an existing connection
    And "Bob" has an issued <Schema_name> credential <Credential_data> from "Acme"
    Then "Acme" revokes the credential
    And "Bob" has the credential issued

    @WalletType_Askar
    Examples:
       | Acme_capabilities                        | Bob_capabilities  | Schema_name    | Credential_data          |
       | --revocation --public-did --mediation    | --mediation       | driverslicense | Data_DL_NormalizedValues |

    @WalletType_Askar_AnonCreds
    Examples:
       | Acme_capabilities                        | Bob_capabilities  | Schema_name    | Credential_data          |
       | --revocation --public-did --mediation --wallet-type askar-anoncreds | --mediation --wallet-type askar-anoncreds | driverslicense | Data_DL_NormalizedValues |
