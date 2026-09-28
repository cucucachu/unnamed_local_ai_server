import ExpoModulesCore

/// iOS stub (M15-06 is Android first). Secure Enclave pairing is later; no ipa.
public class HomeAIDeviceKeyModule: Module {
  public func definition() -> ModuleDefinition {
    Name("HomeAIDeviceKey")

    AsyncFunction("generateKey") { () -> String in
      throw Exception("ios_not_implemented")
    }

    AsyncFunction("sign") { (_ challengeB64url: String) -> String in
      throw Exception("ios_not_implemented")
    }

    AsyncFunction("publicKey") { () -> String? in
      return nil
    }

    AsyncFunction("hasKey") { () -> Bool in
      return false
    }

    AsyncFunction("deleteKey") { () in
    }
  }
}
