"""PII and Business-context data-class vocabulary (two independent axes)."""

from __future__ import annotations

import re

PII_FIELD_REGEX = re.compile(
    r"\b("
    r"email|emailAddress|e_mail|phone|phoneNumber|phone_number|telephone|mobile|"
    r"firstName|first_name|lastName|last_name|fullName|full_name|"
    r"birthDate|dateOfBirth|date_of_birth|dob|"
    r"ssn|socialSecurityNumber|socialSecurity|nationalId|national_id|"
    r"address|streetAddress|street_address|postalCode|postal_code|"
    r"zipCode|zip_code|"
    r"creditCard|credit_card|cardNumber|card_number|cvv|cvc|"
    r"iban|sortCode|sort_code|accountNumber|account_number|"
    r"passport|passportNumber|passport_number|"
    r"driverLicense|driver_license|"
    r"taxId|tax_id|vatNumber|vat_number|"
    r"ipAddress|ip_address|userAgent|user_agent|"
    r"gender|race|ethnicity|religion|politicalOpinion|"
    r"medicalRecord|medical_record|healthCondition|prescription|"
    r"biometric|fingerprint|faceId|face_id"
    r")\b",
    re.IGNORECASE,
)

PII_CLASSIFICATION: dict[str, str] = {
    "email": "direct-pii", "emailaddress": "direct-pii",
    "phone": "direct-pii", "phonenumber": "direct-pii", "phone_number": "direct-pii",
    "telephone": "direct-pii", "mobile": "direct-pii",
    "firstname": "direct-pii", "first_name": "direct-pii",
    "lastname": "direct-pii", "last_name": "direct-pii",
    "fullname": "direct-pii", "full_name": "direct-pii",
    "address": "direct-pii", "streetaddress": "direct-pii",
    "postalcode": "direct-pii", "zipcode": "direct-pii",
    "ssn": "sensitive", "socialsecuritynumber": "sensitive",
    "creditcard": "sensitive", "cardnumber": "sensitive",
    "cvv": "sensitive", "cvc": "sensitive",
    "iban": "sensitive", "accountnumber": "sensitive",
    "passport": "sensitive", "passportnumber": "sensitive",
    "driverlicense": "sensitive", "taxid": "sensitive",
    "vatnumber": "sensitive",
    "gender": "special-category-gdpr", "race": "special-category-gdpr",
    "ethnicity": "special-category-gdpr", "religion": "special-category-gdpr",
    "politicalopinion": "special-category-gdpr",
    "medicalrecord": "special-category-gdpr",
    "healthcondition": "special-category-gdpr",
    "prescription": "special-category-gdpr",
    "biometric": "special-category-gdpr",
    "fingerprint": "special-category-gdpr", "faceid": "special-category-gdpr",
    "ipaddress": "quasi-id", "useragent": "quasi-id",
    "dob": "special-category-gdpr",
    "dateofbirth": "special-category-gdpr", "birthdate": "special-category-gdpr",
}

DATA_CLASS: dict[str, str] = {
    "messagebody": "tenant-content",
    "message_body": "tenant-content",
    "scannedmessage": "tenant-content",
    "attachmentcontent": "tenant-content",
    "scannedattachment": "tenant-content",
    "scannedurl": "tenant-content",
    "messageheader": "tenant-metadata",
    "policyconfig": "tenant-config",
    "customerpolicy": "tenant-config",
    "dlpclassification": "tenant-content-classified",
    "sandboxverdict": "tenant-derived",
    "apikey": "credential",
    "api_key": "credential",
    "customerapikey": "credential",
    "bearertoken": "credential",
    "refresh_token": "credential",
    "refreshtoken": "credential",
    "datakey": "credential-derived",
    "kmskey": "credential-derived",
    "totpsecret": "credential",
    "smtppassword": "credential",
    "sql": "dangerous-payload",
    "query": "dangerous-payload",
    "dql": "dangerous-payload",
    "cypher": "dangerous-payload",
    "soql": "dangerous-payload",
    "statement": "dangerous-payload",
    "expression": "dangerous-payload",
    "script": "dangerous-payload",
    "command": "dangerous-payload",
    "jsonpath": "dangerous-payload",
    "xpath": "dangerous-payload",
}

# Tenant / credential identifiers — camelCase allowed; case-insensitive match.
TENANT_FIELD_REGEX = re.compile(
    r"\b("
    r"messageBody|message_body|scannedMessage|scanned_message|"
    r"attachmentContent|attachment_content|scannedAttachment|"
    r"scannedUrl|scanned_url|messageHeader|message_header|"
    r"policyConfig|policy_config|customerPolicy|dlpClassification|"
    r"sandboxVerdict|sandbox_verdict|"
    r"apiKey|api_key|customerApiKey|bearerToken|bearer_token|"
    r"refreshToken|refresh_token|dataKey|data_key|kmsKey|totpSecret|"
    r"smtpPassword|smtp_password"
    r")\b",
    re.IGNORECASE,
)

# Dangerous-payload field names — case-sensitive so Java types (`Expression`) are not
# mistaken for variables (`expression`).
_DANGEROUS_PAYLOAD_ALT = "|".join(
    sorted(
        (k for k in DATA_CLASS if DATA_CLASS[k] == "dangerous-payload"),
        key=len,
        reverse=True,
    ),
)
DANGEROUS_PAYLOAD_FIELD_REGEX = re.compile(rf"\b({_DANGEROUS_PAYLOAD_ALT})\b")

# Back-compat alias for callers that still import a single regex.
DATA_CLASS_FIELD_REGEX = TENANT_FIELD_REGEX

# Broad set — data-class-field / dangerous-payload classification in source code
RAW_CODE_PAYLOAD_FIELD_NAMES = frozenset({
    "sql", "query", "dql", "cypher", "soql", "statement", "where",
    "filter", "expression", "script", "command", "code", "eval",
    "program", "template", "condition", "predicate", "jsonpath",
    "xpath", "jq",
})

# Strict set — correlate HTTP handlers with SQL execution (avoids fleet-wide noise)
RAW_SQL_PAYLOAD_FIELD_NAMES = frozenset({
    "sql", "rawSql", "raw_sql", "nativeQuery", "native_query",
    "dql", "cypher", "soql", "statement", "soqlQuery", "soql_query",
})

DANGEROUS_PAYLOAD_CLASSES = frozenset({
    "dangerous-payload",
})


def classify_pii(field: str) -> str:
    """Return the PII sensitivity tier for a field name, or "unknown" if unlisted."""
    return PII_CLASSIFICATION.get(field.lower(), "unknown")


def classify_data_class(field: str) -> str | None:
    """Return the business data-class label for a field name, or None if unlisted."""
    return DATA_CLASS.get(field.lower())


def field_axes(field: str) -> tuple[str | None, str | None]:
    """Return a field name's (PII classification, data class) pair."""
    return classify_pii(field), classify_data_class(field)
