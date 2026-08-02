"""Sink / source pattern tables for v2 extractors."""

from __future__ import annotations

import re

# JDBC / native SQL execution (used for raw-code-payload correlation)
SQL_EXECUTION_SINK_NAMES = frozenset({
    "execute", "executeQuery", "executeUpdate", "executeBatch",
    "query", "update", "batchUpdate",
    "createQuery", "createNativeQuery", "getResultList",
    "raw", "Exec", "Query", "QueryRow", "Queryx",
})

# ORM / persistence helpers — sql family catalogue only, not payload endpoints
SQL_ORM_SINK_NAMES = frozenset({
    "find", "findOne", "insert", "insertOne", "save", "delete",
    "deleteOne", "aggregate", "exec",
})

SQL_SINK_NAMES = SQL_EXECUTION_SINK_NAMES | SQL_ORM_SINK_NAMES

SQL_EXECUTION_CALL_HINTS = (
    "JdbcTemplate",
    "NamedParameterJdbcTemplate",
    "createNativeQuery",
    "createQuery",
    "PreparedStatement",
    "Statement.",
    "executeQuery",
    "SqlSession",
    "session.execute",
    "cursor.execute",
    "db.execute",
)

# Receivers that identify a call as database work regardless of the method name.
# Matched against the *tokens* of the trailing identifier, so `readOnlyJdbcTemplate`
# and `this.userDao` hit while `restTemplate` and `itemClient` do not — a plain
# substring test would match "template" in `restTemplate` and "em" in `itemClient`.
SQL_RECEIVER_NAMES = frozenset({
    "jdbctemplate", "namedparameterjdbctemplate", "entitymanager", "em",
    "session", "sqlsession", "cursor", "conn", "connection", "stmt",
    "statement", "preparedstatement", "callablestatement", "db", "dao",
    "repository", "tx", "datasource", "querydsl",
})

# File-level evidence that a module really does SQL, used to admit a bare
# `execute`/`query`/`update` whose receiver is unrecognised. Both alternatives are
# deliberately about *SQL itself* — a keyword inside a string literal, or a
# database library import.
#
# Neither may be satisfied by a field merely named `sql`: OI-7's fabricated
# `raw-code-payload` findings came from an HTTP proxy that had exactly that and no
# SQL anywhere. String runs are length-bounded (see tests/test_redos_bounds.py).
# Group 1 is the statement text *including* everything up to the closing quote, so
# `sql_parameterisation` can look for placeholders that follow the keyword.
SQL_LITERAL_RX = re.compile(
    r"[\"']([^\"'\n]{0,200}?\b(?:SELECT\b|INSERT\s+INTO\b|DELETE\s+FROM\b"
    r"|UPDATE\s+\w{1,64}\s+SET\b|MERGE\s+INTO\b|UPSERT\b|CREATE\s+TABLE\b"
    r"|TRUNCATE\s+TABLE\b|ALTER\s+TABLE\b)[^\"'\n]{0,400})",
    re.IGNORECASE,
)
SQL_DB_IMPORT_RX = re.compile(
    r"\b(?:java\.sql|javax\.sql|jakarta\.persistence|javax\.persistence"
    r"|org\.springframework\.jdbc|org\.springframework\.data"
    r"|org\.hibernate|org\.jooq|mybatis|jakarta\.jdo"
    r"|sqlalchemy|psycopg2?|pymysql|sqlite3|asyncpg|aiomysql|pyodbc"
    r"|database/sql|gorm\.io|jmoiron/sqlx"
    r"|knex|typeorm|sequelize|pg-promise|better-sqlite3)\b",
)

# Placeholder styles that make a SQL statement parameterised: JDBC `?`, named
# `:param`, printf-style `%s`, and PostgreSQL `$1`.
SQL_PLACEHOLDER_RX = re.compile(r"\?|:[A-Za-z_]\w{0,63}|%\(?[a-z_]*\)?s|\$\d{1,3}")

# Split an identifier into lowercase word tokens across camelCase and snake_case.
_IDENT_TOKEN_RX = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z]*|[a-z]+|\d+")

SQL_SOURCE_RX = [
    (re.compile(r'["\'][^"\']*(?:SELECT|INSERT|UPDATE|DELETE)\b[^"\']*["\']\s*\+'), "concatenated"),
    (re.compile(r"\+\s*['\"][^'\"]*(?:SELECT|INSERT|UPDATE|DELETE)"), "concatenated"),
    (re.compile(r'f["\'][^"\']*(?:SELECT|INSERT|UPDATE|DELETE)'), "python-fstring"),
    (re.compile(r"\$\{[^}]+\}.*(?:SELECT|INSERT|UPDATE|DELETE)", re.I), "template"),
]

def receiver_is_database(receiver: str | None) -> bool:
    """True when a call's receiver names a database handle rather than any object.

    Matches the trailing identifier of a qualified receiver (``this.userDao`` ->
    ``userDao``) against :data:`SQL_RECEIVER_NAMES`, comparing whole identifier,
    single word tokens, and adjacent token pairs. The pair check is what lets
    ``readOnlyJdbcTemplate`` hit on ``jdbctemplate`` while ``restTemplate`` — whose
    only pair is ``resttemplate`` — correctly does not.
    """
    if not receiver:
        return False
    trailing = receiver.rsplit(".", 1)[-1].strip()
    if not trailing:
        return False
    if trailing.lower() in SQL_RECEIVER_NAMES:
        return True
    tokens = [t.lower() for t in _IDENT_TOKEN_RX.findall(trailing)]
    if any(t in SQL_RECEIVER_NAMES for t in tokens):
        return True
    return any(
        a + b in SQL_RECEIVER_NAMES for a, b in zip(tokens, tokens[1:])
    )


def file_has_sql_evidence(source: str) -> bool:
    """True when the file contains SQL text or imports a database library.

    Used to admit a SQL-verb call whose receiver is unrecognised. Deliberately
    *not* satisfied by an identifier named ``sql``: OI-7's false positives came
    from an HTTP proxy with a ``sql`` field and no SQL in it.
    """
    return bool(SQL_LITERAL_RX.search(source) or SQL_DB_IMPORT_RX.search(source))


def sql_parameterisation(call_text: str, source: str) -> bool | str:
    """Classify a SQL call as parameterised, raw, or ``"unknown"``.

    Returns ``"unknown"`` when no SQL statement is in scope — there is nothing to
    be parameterised or not. 1.1.0 tested ``"?" in call_text or ":" in call_text``,
    which labelled calls containing no SQL at all as *unparameterised* and so made
    OI-7's false positives read like findings.
    """
    statements = [m.group(1) for m in SQL_LITERAL_RX.finditer(call_text)]
    if not statements:
        statements = [m.group(1) for m in SQL_LITERAL_RX.finditer(source)]
    if not statements:
        return "unknown"
    return any(SQL_PLACEHOLDER_RX.search(s) for s in statements)


FILE_SINK_RX: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"Files\.(write|writeString|delete|move|copy)\s*\("), "java-nio"),
    (re.compile(r"new\s+FileOutputStream\s*\("), "java-io"),
    (re.compile(r"FileWriter\s*\("), "java-io"),
    (re.compile(r"ZipInputStream|TarArchiveInputStream"), "archive-extract"),
    (re.compile(r"fs\.(writeFile|writeFileSync|createWriteStream|unlink)\s*\("), "node-fs"),
    (re.compile(r"open\s*\([^)]*['\"][wa]"), "python-open"),
    (re.compile(r"Path\.(write_text|write_bytes|unlink)\s*\("), "python-pathlib"),
    (re.compile(r"shutil\.(copy|move|rmtree)\s*\("), "python-shutil"),
    (re.compile(r"os\.(Create|Remove|Rename)\s*\("), "go-os"),
    (re.compile(r"ioutil\.WriteFile\s*\("), "go-ioutil"),
]

HTTP_OUT_RX: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"RestTemplate\.(get|post|put|delete|exchange)"), "java", "client-call"),
    (re.compile(r"WebClient\.(get|post|put|delete)"), "java", "client-call"),
    (re.compile(r"OkHttpClient|\.newCall\s*\("), "java", "client-call"),
    (re.compile(r"requests\.(get|post|put|delete|patch)\s*\("), "python", "client-call"),
    (re.compile(r"httpx\.(get|post|put|delete|patch)\s*\("), "python", "client-call"),
    (re.compile(r"aiohttp\.ClientSession"), "python", "client-call"),
    (re.compile(r"urllib\.request\.urlopen\s*\("), "python", "client-call"),
    (re.compile(r"\bfetch\s*\("), "javascript", "client-call"),
    (re.compile(r"axios\.(get|post|put|delete|patch)"), "javascript", "client-call"),
    (re.compile(r"http\.NewRequest\s*\("), "go", "client-call"),
]

HTTP_IN_RX: dict[str, list[tuple[re.Pattern[str], str]]] = {
    "java-kotlin": [
        (re.compile(
            r'@(GetMapping|PostMapping|PutMapping|DeleteMapping|PatchMapping|RequestMapping)\s*\(\s*(?:value\s*=\s*)?(?:\{?\s*)?"([^"]*)"'
        ), "spring"),
        (re.compile(r'@(?:Get|Post|Put|Delete|Patch)\s*\(\s*(?:uri\s*=\s*)?"([^"]*)"'), "jax-rs"),
        (re.compile(r'@Path\s*\(\s*"([^"]+)"'), "jax-rs"),
    ],
    "python": [
        (re.compile(r'@app\.route\s*\(\s*["\']([^"\']+)["\']'), "flask"),
        (re.compile(r'@router\.(get|post|put|delete|patch)\s*\(\s*["\']([^"\']+)["\']'), "fastapi"),
    ],
    "javascript": [
        (re.compile(r'\.(get|post|put|delete|patch)\s*\(\s*["\']([^"\']+)["\']'), "express"),
    ],
    "go": [
        (re.compile(r'\.(GET|POST|PUT|DELETE|PATCH)\s*\(\s*"([^"]+)"'), "gin"),
    ],
}

QUEUE_RX: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r'@KafkaListener[^)]*topics\s*=\s*"([^"]+)"'), "consume", "kafka"),
    (re.compile(r'kafkaTemplate\.send\s*\(\s*"([^"]+)"'), "produce", "kafka"),
    (re.compile(r'@RabbitListener[^)]*queues\s*=\s*"([^"]+)"'), "consume", "rabbitmq"),
    (re.compile(r'rabbitTemplate\.(convertAndSend|send)\s*\(\s*"([^"]+)"'), "produce", "rabbitmq"),
    (re.compile(r'@SqsListener\s*\(\s*"([^"]+)"'), "consume", "sqs"),
    (re.compile(r'sqsClient\.sendMessage'), "produce", "sqs"),
    (re.compile(r'redisTemplate\.opsForStream\(\)\.add'), "produce", "redis-stream"),
    (re.compile(r'\.publish\s*\(\s*"([^"]+)"'), "produce", "nats"),
    (re.compile(r'SnsClient\.publish'), "produce", "sns"),
    (re.compile(r'@JmsListener'), "consume", "jms"),
]

CRYPTO_RX: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r'MessageDigest\.getInstance\s*\(\s*"([^"]+)"'), "hash"),
    (re.compile(r'Cipher\.getInstance\s*\(\s*"([^"]+)"'), "cipher"),
    (re.compile(r'BCryptPasswordEncoder|Argon2PasswordEncoder'), "password-hash"),
    (re.compile(r'hashlib\.(md5|sha1|sha256)'), "hash"),
    (re.compile(r'crypto\.create(Hash|Cipheriv|Hmac)'), "node-crypto"),
]

SECRETS_MANAGER_RX: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r'SecretsManagerClient|getSecretValue'), "aws-secrets-manager"),
    (re.compile(r'KmsClient|generateDataKey|\.decrypt\s*\('), "aws-kms"),
    (re.compile(r'SsmClient|getParameter'), "aws-parameter-store"),
    (re.compile(r'Vault\.|VaultTemplate'), "hashicorp-vault"),
    (re.compile(r'SecretManagerServiceClient|accessSecretVersion'), "gcp-secret-manager"),
    (re.compile(r'SecretClient\.getSecret'), "azure-key-vault"),
]

PII_LOG_RX = re.compile(
    r"(?:log(?:ger)?|LOG)\.(?:info|warn|error|debug|trace)\s*\(",
    re.IGNORECASE,
)

PII_STORAGE_RX: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\.save\s*\(|\.persist\s*\(|insertOne\s*\("), "persistence"),
    (re.compile(r"putObject\s*\("), "s3"),
    (re.compile(r"sendEmail|messages\.create|mail\.send"), "third-party-comms"),
]

AUTH_RX: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"@PreAuthorize"), "spring-pre-authorize"),
    (re.compile(r"@Secured\s*\(\s*\{\s*\"ROLE_ANONYMOUS\""), "secured-anonymous"),
    (re.compile(r"@Secured\s*\(\s*\{\s*\"ROLE_"), "secured-role"),
    (re.compile(r"IS_AUTHENTICATED"), "secured-authenticated"),
    (re.compile(r"@PermitAll"), "permit-all"),
    (re.compile(r"@RolesAllowed"), "jaxrs-roles-allowed"),
    (re.compile(r"csrf\(\)\.disable"), "csrf-disabled"),
]

RAW_PAYLOAD_BODY_RX = re.compile(
    r"(?:@RequestBody|RequestBody|BaseModel|class\s+\w+).*?"
    r"\b(sql|query|dql|cypher|soql|statement|expression|script|command)\b",
    re.IGNORECASE | re.DOTALL,
)
