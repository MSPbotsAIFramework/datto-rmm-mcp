from .._json import error_envelope

NO_TOKEN = error_envelope(
    "not_configured",
    "No usable Datto RMM credentials. Send X-Datto-Api-Key and X-Datto-Api-Secret, "
    "and a valid X-Datto-Platform (concord, merlot, pinotage, syrah, vidal, zinfandel) "
    "or X-Datto-Api-Url.",
    False,
)
