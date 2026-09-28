package ai.homeai.devicekey

import android.os.Build
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import expo.modules.kotlin.exception.CodedException
import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.PrivateKey
import java.security.Signature
import java.security.spec.ECGenParameterSpec

private const val ALIAS = "homeai-host-pair"
private const val ANDROID_KEYSTORE = "AndroidKeyStore"

class HomeAIDeviceKeyModule : Module() {
  override fun definition() = ModuleDefinition {
    Name("HomeAIDeviceKey")

    AsyncFunction("generateKey") {
      deleteAlias()
      val builder = KeyGenParameterSpec.Builder(
        ALIAS,
        KeyProperties.PURPOSE_SIGN or KeyProperties.PURPOSE_VERIFY
      )
        .setAlgorithmParameterSpec(ECGenParameterSpec("secp256r1"))
        .setDigests(KeyProperties.DIGEST_SHA256)
        .setUserAuthenticationRequired(true)
        .setInvalidatedByBiometricEnrollment(false)
      if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
        builder.setUserAuthenticationParameters(
          30,
          KeyProperties.AUTH_BIOMETRIC_STRONG or KeyProperties.AUTH_DEVICE_CREDENTIAL
        )
      } else {
        @Suppress("DEPRECATION")
        builder.setUserAuthenticationValidityDurationSeconds(30)
      }
      val generator = KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_EC, ANDROID_KEYSTORE)
      generator.initialize(builder.build())
      val pair = generator.generateKeyPair()
      b64url(pair.public.encoded)
    }

    AsyncFunction("sign") { challengeB64url: String ->
      val challenge = b64urlDecode(challengeB64url)
      val store = KeyStore.getInstance(ANDROID_KEYSTORE).apply { load(null) }
      val privateKey = store.getKey(ALIAS, null) as? PrivateKey
        ?: throw CodedException("no_device_key", "No host-app key is enrolled on this device", null)
      val signature = Signature.getInstance("SHA256withECDSA")
      signature.initSign(privateKey)
      signature.update(challenge)
      b64url(signature.sign())
    }

    AsyncFunction("publicKey") {
      val store = KeyStore.getInstance(ANDROID_KEYSTORE).apply { load(null) }
      val cert = store.getCertificate(ALIAS) ?: return@AsyncFunction null
      b64url(cert.publicKey.encoded)
    }

    AsyncFunction("hasKey") {
      val store = KeyStore.getInstance(ANDROID_KEYSTORE).apply { load(null) }
      store.containsAlias(ALIAS)
    }

    AsyncFunction("deleteKey") {
      deleteAlias()
    }
  }

  private fun deleteAlias() {
    val store = KeyStore.getInstance(ANDROID_KEYSTORE).apply { load(null) }
    if (store.containsAlias(ALIAS)) {
      store.deleteEntry(ALIAS)
    }
  }

  private fun b64url(data: ByteArray): String {
    return Base64.encodeToString(data, Base64.NO_WRAP or Base64.URL_SAFE or Base64.NO_PADDING)
  }

  private fun b64urlDecode(text: String): ByteArray {
    val padded = text + "=".repeat((4 - text.length % 4) % 4)
    return Base64.decode(padded, Base64.URL_SAFE)
  }
}
