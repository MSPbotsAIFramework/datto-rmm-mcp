from .._json import error_envelope

NO_TOKEN = error_envelope(
    "not_configured",
    "No Datto RMM credentials. Send the X-Datto-Api-Key, X-Datto-Api-Secret, "
    "and X-Datto-Api-Url headers.",
    False,
)
