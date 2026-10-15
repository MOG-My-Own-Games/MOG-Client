from mog_client import ed25519

# RFC 8032, section 7.1
VECTORS = [
    (
        "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
        "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a",
        "",
        "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b",
    ),
    (
        "4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
        "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c",
        "72",
        "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00",
    ),
]


def test_the_rfc_test_vectors_sign_and_verify():
    for seed, public, message, signature in VECTORS:
        seed, public, message, signature = (bytes.fromhex(x) for x in (seed, public, message, signature))
        assert ed25519.public_key(seed) == public
        assert ed25519.sign(seed, message) == signature
        assert ed25519.verify(public, message, signature)


def test_a_changed_message_signature_or_key_is_refused():
    seed, public, message, signature = (bytes.fromhex(x) for x in VECTORS[1])
    assert not ed25519.verify(public, b"\x73", signature)
    assert not ed25519.verify(public, message, signature[:-1] + b"\x01")
    assert not ed25519.verify(bytes.fromhex(VECTORS[0][1]), message, signature)
    assert not ed25519.verify(public, message, b"")
    assert not ed25519.verify(b"short", message, signature)
