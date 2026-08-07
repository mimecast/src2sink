"""Sink / source pattern tables for v2 extractors."""

from __future__ import annotations

import re
from collections.abc import Iterator

from ..known_api_clients import get_bindings
from ..vocabulary import RAW_SQL_PAYLOAD_FIELD_NAMES
from .symbols import build_symbol_table, iter_concatenated_symbols

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


# Receivers we recognise as belonging to *another* boundary — an HTTP client, a
# digest, a task executor. This is positive knowledge, not a guess at everything
# that is not a database: naming what a receiver *is* lets file-level evidence be
# overruled without touching the unknown-receiver case that evidence exists for
# (OI-26). `OI-20` generalises this into the boundary catalogue.
#
# Deliberately absent: `mapper`. A MyBatis mapper genuinely is a database
# receiver, so listing it would withdraw real findings to remove false ones.
NON_DATABASE_RECEIVER_NAMES = frozenset({
    "httpclient", "resttemplate", "webclient", "restclient", "okhttpclient",
    "messagedigest", "digest", "cipher", "mac", "signature",
    "executor", "executorservice", "threadpool", "pool", "scheduler",
    "cache", "logger",
})


def receiver_is_another_boundary(receiver: str | None) -> bool:
    """True when a receiver reads as some *other* kind of boundary than a database.

    Used to stop file-scoped SQL evidence overruling local evidence about the
    call itself. An unknown receiver stays unknown — this answers "is it
    something else", never "is it not a database".
    """
    if not receiver:
        return False
    trailing = receiver.rsplit(".", 1)[-1]
    return trailing.lower() in NON_DATABASE_RECEIVER_NAMES

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
    "statement", "preparedstatement",
    # Ordinary abbreviations for a PreparedStatement/CallableStatement. Absent
    # while `stmt` and `conn` were present, which is why tightening the file-scope
    # rule needed these first — otherwise the OI-26 guard would start rejecting
    # the very calls it exists to catch.
    "ps",
    "pstmt",
    "cstmt", "callablestatement", "db", "dao",
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
# Any quoted literal, used to confine the placeholder search to string contents.
_STRING_LITERAL_RX = re.compile(r'"[^"\n]{0,400}"|\'[^\'\n]{0,400}\'')

# Split an identifier into lowercase word tokens across camelCase and snake_case.
_IDENT_TOKEN_RX = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z]*|[a-z]+|\d+")

_SQL_KW = r"(?:SELECT|INSERT|UPDATE|DELETE)"
_MAX_SQL_LITERAL = 400
# Markers that a literal is being interpolated rather than used verbatim:
# Kotlin/JS `${expr}` and `$ident`, printf `%s`/`%(name)s`, and `{}`/`{name}`.
_INTERPOLATION = (
    r"(?:\$\{[^}\n]{1,120}\}"
    r"|\$[A-Za-z_]\w{0,63}"
    r"|%\(?[A-Za-z_]{0,63}\)?[sd]"
    r"|\{[A-Za-z_0-9]{0,63}\})"
)


def _sql_literal(quote: str) -> str:
    """A quoted literal containing a SQL keyword, bounded on both sides.

    The body excludes only the delimiter *in use*. 1.1.0 excluded both quote
    characters, so a double-quoted literal containing an apostrophe could not be
    spanned — and `"… WHERE ref = '" + ref + "'"` is precisely how a
    string-built query with a quoted parameter looks, which is the shape the
    pattern most needed to catch (OI-8).
    """
    body = rf"[^{quote}\n]{{0,{_MAX_SQL_LITERAL}}}"
    return rf"{quote}{body}?\b{_SQL_KW}\b{body}"


def _sql_source_patterns() -> list[tuple[re.Pattern[str], str]]:
    """Build the dynamic-SQL patterns for each quote style.

    Generated per delimiter so the bounded literal body is defined once rather
    than repeated (and mis-repeated) across a dozen literal regexes.
    """
    out: list[tuple[re.Pattern[str], str]] = []
    for q in ('"', "'"):
        lit = _sql_literal(q)
        body = rf"[^{q}\n]{{0,{_MAX_SQL_LITERAL}}}"
        out += [
            # "SELECT …" + x   /   x + "SELECT …"
            (re.compile(rf"{lit}{q}\s*\+"), "concatenated"),
            (re.compile(rf"\+\s*{lit}"), "concatenated"),
            (re.compile(rf"f{lit}"), "python-fstring"),
            # String.format("SELECT …", x) / MessageFormat.format(…)
            (re.compile(rf"(?:String|MessageFormat)\.format\s*\(\s*{lit}"), "format-call"),
            # "SELECT …".formatted(x) / "SELECT …".format(x)
            (re.compile(rf"{lit}{q}\s*\.\s*format(?:ted)?\s*\("), "format-call"),
            # "SELECT …" % x
            (re.compile(rf"{lit}{q}\s*%\s*[(A-Za-z_]"), "format-percent"),
            # A keyword and an interpolation inside one literal, in either order.
            # 1.1.0 required the interpolation first, so `"SELECT … ${id}"` — the
            # way templates are actually written — never matched.
            (re.compile(rf"{q}{body}?\b{_SQL_KW}\b{body}?{_INTERPOLATION}"), "template"),
            (re.compile(rf"{q}{body}?{_INTERPOLATION}{body}?\b{_SQL_KW}\b"), "template"),
        ]
    return out


SQL_SOURCE_RX = _sql_source_patterns()

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


def payload_field_names() -> tuple[frozenset[str], frozenset[str]]:
    """Return (vocabulary fields, binding-declared fields) for outbound payloads.

    The strict vocabulary is a generic guess; a binding's ``payload_fields`` is a
    *declaration* that this particular service treats that field as executable
    input, which is why the two are kept apart — the second earns higher
    confidence than the first.
    """

    declared = frozenset(f for b in get_bindings() for f in b.payload_fields if f)
    return frozenset(RAW_SQL_PAYLOAD_FIELD_NAMES), declared


def iter_bound_payload_fields(source: str) -> Iterator[tuple[int, str, bool]]:
    """Yield ``(offset, field_name, declared_by_binding)`` for payload fields being set.

    The existing field passes recognise *declarations* (``private String sql;``),
    so ``body.setSql(sqlText)`` contributed nothing at all (OI-9). A payload is
    populated at the call site, in one of four shapes::

        body.setSql(x)    builder().sql(x)    body.sql = x    {"sql": x}

    Matching the binding-declared names separately is what lets a service that
    declares ``payload_fields: ["dql"]`` be recognised without widening the
    vocabulary for every other repo in the fleet.
    """
    vocabulary, declared = payload_field_names()
    names = vocabulary | declared
    if not names:
        return
    alt = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    rx = re.compile(
        rf"\.\s*set({alt})\s*\("        # body.setSql(x)
        rf"|\.\s*({alt})\s*\("          # builder().sql(x)
        rf"|\.\s*({alt})\s*="           # body.sql = x
        rf"|[\"']({alt})[\"']\s*:",     # {"sql": x}
        re.IGNORECASE,
    )
    # `setSql` carries the field as `Sql`, so matching is case-insensitive and the
    # canonical spelling is reported — otherwise the same field appears under two
    # names depending on how it happened to be bound.
    canonical = {n.lower(): n for n in names}
    declared_lower = {d.lower() for d in declared}
    for m in rx.finditer(source):
        matched = next(g for g in m.groups() if g is not None)
        lowered = matched.lower()
        yield m.start(), canonical.get(lowered, matched), lowered in declared_lower


def sql_symbol_table(source: str) -> dict[str, str]:
    """Map identifier -> SQL-shaped string literal declared in this file.

    The base query of a hand-written DAO usually lives in a constant while only
    the clause appended to it is dynamic. Every pattern in :data:`SQL_SOURCE_RX`
    anchors on a keyword *inside the literal next to the operator*, so
    ``SAFE + " AND ref = '" + ref`` matched nothing at all: the keyword is in
    ``SAFE`` and the concatenated fragments carry none (OI-11).
    """
    return build_symbol_table(
        source,
        # The value arrives unquoted; re-quote it so the same literal pattern
        # judges it, keeping one definition of what counts as a SQL statement.
        lambda value: bool(SQL_LITERAL_RX.search(f'"{value}"')),
    )


def _statement_is_constructed(region: str, symbols: dict[str, str] | None = None) -> bool:
    """True if ``region`` shows SQL being assembled rather than used verbatim.

    ``symbols`` resolves constant-mediated construction: a SQL constant taking
    part in a concatenation is a constructed statement even though none of the
    concatenated literals carries a keyword.
    """
    if any(pat.search(region) for pat, _kind in SQL_SOURCE_RX):
        return True
    if not symbols:
        return False
    return any(True for _ in iter_concatenated_symbols(region, symbols))


def sql_parameterisation(
    call_text: str, source: str, symbols: dict[str, str] | None = None
) -> str:
    """Classify the posture of the SQL statement executed at this call site.

    ``parameterised`` is not a safety verdict, because a placeholder does not undo
    a concatenation in the same statement — ``"… ref = '" + ref + "' AND id = ?"``
    is injectable despite the ``?``. So two independent facts are reported as one
    posture (OI-10):

    ==================  ============  =========================
    posture             placeholders  constructed
    ==================  ============  =========================
    ``parameterised``   yes           no
    ``mixed``           yes           yes
    ``raw``             no            yes
    ``static``          no            no
    ``unknown``         statement not attributable to this call site
    ==================  ============  =========================

    The governing rule is that **weak evidence may downgrade a posture, never
    establish the safe one.** A statement found at the call site is a fact about
    this call; a literal found elsewhere in the file is a guess, so it is only
    trusted when the file builds no SQL dynamically *and* holds exactly one
    candidate statement. Anything less resolves to ``unknown``.
    """
    if SQL_LITERAL_RX.search(call_text):
        region = call_text
    else:
        # The call executes a variable. Constant-mediated SQL is the normal Java
        # shape, so the file is worth consulting — but only when it cannot
        # mislead. One candidate statement is attributable; several are a guess
        # about which one runs here, and that guess is what let an unrelated safe
        # constant certify an injectable call site (OI-10).
        candidates = SQL_LITERAL_RX.findall(source)
        if len(candidates) != 1:
            return "unknown"
        region = source

    # Placeholders are looked for inside string literals only: a bare `?` in the
    # surrounding code is as likely to be a ternary as a bind parameter.
    placeholders = any(
        SQL_PLACEHOLDER_RX.search(lit) for lit in _STRING_LITERAL_RX.findall(region)
    )
    if _statement_is_constructed(region, symbols):
        return "mixed" if placeholders else "raw"
    return "parameterised" if placeholders else "static"


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

# Frameworks whose inbound pattern is *not* anchored to a route-declaration
# marker — an annotation, or a required router receiver. A match from one of
# these is a weaker claim, and saying so is the change that would have surfaced
# `OI-37` without a fleet run: the JavaScript pattern reported `high` while
# 10,225 of its 10,245 fleet matches were reactive-form access, Cypress
# selectors, cache writes and outbound calls.
#
# `gin` relies on Go's uppercase-verb convention rather than on an anchor. Only
# 8 nodes in the observed fleet, so there is no evidence either way — but it
# should not claim the confidence an annotation earns.
UNANCHORED_HTTP_IN = frozenset({"gin"})


def http_in_confidence(framework: str) -> str:
    """How much a match by this framework's pattern is worth.

    Derived from the pattern's anchoring rather than hardcoded, so a new
    unanchored pattern has to declare itself instead of inheriting `high`.
    """
    return "medium" if framework in UNANCHORED_HTTP_IN else "high"


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
        # The receiver is required. Every sibling here is anchored to something
        # meaning "route declaration" — Flask to `@app.route`, FastAPI to
        # `@router.`, JAX-RS and Spring to an annotation. This one began at the
        # dot, so it matched a verb-named call on *any* receiver, in a language
        # where `.get(key)` is ubiquitous for reasons unrelated to HTTP.
        #
        # Measured over a 746-repo fleet: 10,245 matches, of which **20** were
        # routes. The rest were Angular reactive-form field access (4,391),
        # Cypress selectors (1,104), header and cache lookups (646) and — worst —
        # 605 *outbound* client calls recorded as doors into the service. See
        # `OI-37`.
        (re.compile(
            r'\b(?:app|router|server|api|fastify|_router|[A-Za-z_$][\w$]*[Rr]outer)'
            r'\s*\.\s*(get|post|put|delete|patch)\s*\(\s*["\']([^"\']+)["\']'
        ), "express"),
    ],
    "go": [
        (re.compile(r'\.(GET|POST|PUT|DELETE|PATCH)\s*\(\s*"([^"]+)"'), "gin"),
    ],
}

# Ways into a service that are not HTTP and are not a queue. Each is an
# *observation* — a marker that this code is reachable from outside by some
# mechanism — and the entry-point derivation decides what that means (OI-21).
#
# Every run is length-bounded, because these read untrusted scanned source
# (TA-005). `externally_triggered` is False only for `schedule`: a cron job is a
# front door nobody outside chooses to open, so it carries no untrusted input by
# that route and a reachability answer must be able to tell it apart.
ENTRY_MARKER_RX: list[tuple[re.Pattern[str], str, bool]] = [
    # gRPC: the service annotation, and the generated base class a service extends.
    (re.compile(r"@GrpcService\b"), "grpc", True),
    (re.compile(r"\bextends\s+\w{1,80}Grpc\.\w{1,80}ImplBase\b"), "grpc", True),
    (re.compile(r"@(?:GRpcService|GrpcAdvice)\b"), "grpc", True),
    # GraphQL: Spring for GraphQL, and the DGS annotations.
    (re.compile(r"@(?:QueryMapping|MutationMapping|SubscriptionMapping)\b"), "graphql", True),
    (re.compile(r"@Dgs(?:Query|Mutation|Subscription|Data)\b"), "graphql", True),
    (re.compile(r"@SchemaMapping\b"), "graphql", True),
    # Scheduled work. Triggered by the clock, not by a caller.
    (re.compile(r"@Scheduled\b"), "schedule", False),
    (re.compile(r"@DisallowConcurrentExecution\b"), "schedule", False),
    (re.compile(r"@app\.task\b"), "schedule", False),
    # Command-line input.
    (re.compile(r"\bargparse\.ArgumentParser\s*\("), "cli", True),
    (re.compile(r"\bsys\.argv\b"), "cli", True),
    (re.compile(r"\bpublic\s+static\s+void\s+main\s*\(\s*String\s*(?:\[\]\s*\w{1,80}|\w{1,80}\s*\[\])"), "cli", True),
    (re.compile(r"@click\.command\b"), "cli", True),
    # Filesystem input.
    (re.compile(r"@FileWatch\b|\bWatchService\b"), "file-watch", True),
]

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
