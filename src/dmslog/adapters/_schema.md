# Adapter template files (schema 1)

One TOML file per log family in this folder. `dmslog.parse.templates.load_catalog()` reads every
`*.toml` here (through `importlib.resources`, so it also works inside `dmslog.pyz`), except
`_example.toml`, which is documentation. A minimal file using every feature is `_example.toml`.

Files are read in name order, but the result never depends on that order: template codes are
global and unique, and each family has a fixed priority (below).

## File

| Key | Type | Required | Meaning |
|---|---|---|---|
| `schema` | int | no (1) | Schema version. Only `1` is accepted. |
| `family` | str | yes | Family code, as in `families.toml` (`AWFM`, `AONT`, `BLTHOST`, ...). A name starting with `_` is a **library** (`_common`): it is never a stream family by itself and can only be used through `extends`. |
| `description` | str | no | Free text. |
| `extends` | list of str | no | Libraries tried after this file's templates, in this order. Libraries cannot extend. |
| `[payload_defaults]` | table | no | `role = { direction = "in"\|"out", peer = "NAME" }`: direction and other side for payloads whose template does not set them. Roles: `request`, `response`, `fault`, `delta`, `content`, `publication`, `other`. |
| `[[template]]` | array of tables | no | Templates, in priority order. |
| `[[operation]]` | array of tables | no | Operation life-cycle rules for the derivers (ARQUITETURA 6.5). |
| `[[sensitive_pair]]` | array of tables | no | Normal-to-sensitive pairing rules (ARQUITETURA 8). |
| `[[link_rule]]` | array of tables | no | Callback, retry and response link rules inside a collection (ARQUITETURA 8). |

Unknown keys are errors (typos never pass silently).

`[[operation]]` and `[[sensitive_pair]]` are free-form tables owned by the deriver tasks. The
loader only checks that the values of `start`, `end`, `end_ok`, `end_fail`, `fail`, `normal`,
`sensitive` and `codes` (a code or a list of codes) name templates visible to the family (its own
or its libraries'); aliases are accepted and replaced by the canonical code.

### Correlation rules (M5)

`[[sensitive_pair]]`: `normal` (code of the normal record that points to the sensitive log),
`sensitive` (code of the paired sensitive record), `match` (`["pid", "tid"]` by default: fields that
must be equal), `max_dt_ms` (default 1000: the sensitive record is the next one of that code at most
this far after the normal one, in order), optional `key` (field name present on both templates; when
equal on both sides the link has confidence 3).

`[[link_rule]]`: `kind` (`callback`, `retry` or `response`), `a` and `b` (a code or a list of codes:
the first and the later record), `key` (an `entry_key` type or a field name that must be equal on
both sides; required for `callback`, `retry` and `response`, except when `window_only = true`),
`scope` (`stream`, the default, or `group`: normal and sensitive streams of the same process),
`max_dt_ms` (default 60000), `window_only` (`true` = no shared key: heuristic by time window,
stored with confidence 0) and `origin` (`record`, the default, or `operation`: keyed `callback`/`response`
rules only, with disjoint `a` and `b`; the link starts at the start record of the single operation that
holds every `a` record of the key value, with confidence 2, and nothing is linked when that operation is
not unique; see `derive/links.py`). Rules of one family never link records of another family.

Families without a file (`UNKNOWN`, service logs, ...) get the `_common` library.

## Template

| Key | Type | Required | Meaning |
|---|---|---|---|
| `code` | str | yes | Unique ID, `ABC-001` style: `[A-Z][A-Z0-9]*(-[A-Z0-9]+)*`, up to 32 characters. Stored in `template.code`. |
| `aliases` | list of str | no | Old IDs of the same template (for example `WFM-090` for `GEN-STA-02`). Resolved by `Catalog.resolve()`. Unique across codes and aliases. |
| `class` | str | yes | `start`, `end`, `req_resp`, `validation`, `error`, `statistics`, `startup`, `lifecycle`, `other`. |
| `sev` | int 1..5 | no | Severity hint: 1 critical, 2 error, 3 warning, 4 info, 5 noise. The final severity is decided by `parse/severity.py` (ARQUITETURA 6.4). |
| `sev_field` + `sev_map` | str + table | no | Severity hint taken from a field: `sev_field = "vsev"`, `sev_map = { Critical = 2, Error = 2, Warning = 3, Info = 4 }`. A value not in the map falls back to `sev`. |
| `title` | str | no | Short human name. |
| `regex` | str | one of | Python regex matched with `re.match` against the **stripped** first line (`msg`). Use TOML literal strings (`'...'` or `'''...'''`). |
| `window` | int | no (4096) | Regex templates only: the template applies only to stripped lines of at most `window` characters (128..4096). Declare a smaller window when the regex has a quadratic worst case (see "Regex safety"); a longer line is then not matched by this template. |
| `text` | str | one of | Exact text of the stripped first line. Fastest (one dict lookup); exact texts are tried **before** every regex. |
| `fields` | table | no | Field types: `name = "str"\|"int"\|"hex"\|"guid"\|"float"\|"list"`. Names are regex groups or continuation outputs. Untyped fields are `str`. |
| `keys` | list | no | Fields copied to `entry_key`: a field name (key type = field name) or `{ field = "msg_id", type = "msgid" }`. List fields give one key per item. |
| `refs_sensitive` | bool | no (false) | The record points to the sensitive log (`flags & 4`). |
| `fts` | bool | no (true) | `false` keeps the records out of the full-text index (Verbose tracing). |
| `stream` | str | no (`any`) | `normal`, `sensitive` or `any`: which stream kind the template applies to. |
| `cont` | str or table | no | Continuation parser: `cont = "dotnet_stack"` or `cont = { parser = "...", option = value }`. |
| `payload` | table | no | Where the payload is (below). |
| `examples` | list | no | Positive examples: a message string, or `{ msg = "...", cont = "...", fields = { ... } }` (expected subset of the fields, after typing). |
| `counter_examples` | list | no | Messages that must not be classified as this template: string or `{ msg, cont }`. |

### Matching rules

- The first line is stripped (spaces and TABs on both sides) before matching; `match_end` in the
  result is an offset in the original line.
- A template applies only to messages that **fit its window**: the stripped line must have at most
  4096 characters (`MAX_MATCH_CHARS`, default and maximum) or the smaller `window` it declares. A
  longer line is not matched by that template (not even a prefix template), and the record falls
  through to the following candidates (or stays unknown). The regex never sees a cut text, so
  greedy captures, `$` and lookaheads behave exactly as on the full line; a line of exactly
  `window` characters matches normally. A line break ends the matched text (records never hold
  one). `line_regex` continuation parsers skip a line longer than 4096 characters the same way. A continuation line that the 256 KiB continuation cap would cut is dropped (no parser sees a partial line).
- Priority: exact texts first; then regex templates in order (the file's own templates, then each
  library of `extends`). The first match wins.
- Every capture group must be named: `(?P<name>...)`, lowercase snake_case; use `(?:...)` for
  grouping. Empty captures stay `""`; groups that did not participate are omitted.
- Typing: `int` (up to 18 digits, longer stays text), `hex` (`0x1a8` or `1a8` -> integer),
  `guid` (lowercase), `float`, `list` (comma separated, quotes and spaces trimmed, at most 64
  items). A value that does not convert stays text. A record never fails because of a field.
- Write the fixed text at the start when possible (`^\[GetIncidents start\] - ...`): the literal
  prefix (and each alternative of a leading `(?:A|B)`) is used for dispatch. Prefix-less regexes
  work too; they are filtered by their first character and by their longest literal.

### Regex safety (checked at load, `UnsafeRegexError`)

Threat model: template regexes are trusted content written by the maintainers (the untrusted data
is the log text). The validator exists so that an accidental pathological regex cannot freeze
ingestion with catastrophic backtracking; it does not try to stop a hostile regex author.

Strategy: bound the damage by construction. Each regex gets a conservative bound on the steps of
one `re.match` on a text of its window, and that bound must fit a fixed budget. Rules
(`_check_regex` in `parse/templates.py`):

1. Allowed: literals, `.`, character classes, anchors (`^ $ \A \Z \b \B`), named and
   non-capturing groups, alternation, greedy and lazy repeats with bounds up to 64 (or unbounded),
   lookarounds. Rejected: inline or scoped flags (`(?i)`, `(?i:...)`; there is no case-insensitive
   mode), possessive repeats, atomic groups, backreferences, conditionals, unnamed groups, more
   than 1000 characters or 24 capture groups, warnings at compile time. Whitelist limits on
   constructs whose constant factors the work model does not count: a character class has at most
   32 items (ranges, literals and categories count 1 each) and capture groups nest at most 3 deep
   (the catalog needs 4 items, depth 2 and 8 groups).
2. A repeat that can iterate more than once (`+`, `*`, `{m,n}` with n > 1) repeats only
   one-character tokens: `\d+`, `[\d,]+`, `(?:ab)*`, `[0-9a-f]{36}`. Nothing optional, repeated
   or alternative inside it: `(?:\d+,)*`, `(?:a|b)+`, `(?:x?)+`, `(?:a{1,64})+` are rejected; use
   `[\d,]+` or `\d+(?:,\d+)?`.
3. Work budget: the bound `work`, evaluated at the window, is at most `MAX_REGEX_STEPS`
   (2^21, about 2.1 million steps). `work` is a polynomial in N = n + 1 (n: text length):
   - in sequence, `work(PR) = work(P) + out(P) x work(R)`, where `out(P)` counts the ways P can end
     with a next character that R can start with (exact code point sets over all of Unicode;
     `\d \w \s` as the running `re` defines them). `(?P<n>\d+) ` has `out` 1: only the longest
     count is followed by a space. `(?P<a>.*) ` has `out` N;
   - alternatives always add their work (each one may be tried); their `out` is the largest one
     when no two can start with the same character, else the sum;
   - a repeat of one-character tokens reads at most N characters and has at most N counts
     (`hi - lo + 1` when bounded);
   - a lookaround costs the whole work of its body on every visit, and counts as able to start
     with any character: nothing before it, in sequence or among alternatives, is discounted by
     the next character, so its body is paid on every attempt that reaches it;
   - a rest that cannot fail (a greedy `.*` before `$`, the text being one line) is paid once.

   In practice: one ambiguous unbounded repeat (`(?P<a>.*)\.$`) is linear and fits the default
   window; one ambiguous unbounded repeat followed by another scan (`^(?P<a>.+?) - (?P<b>.+)\.$`,
   `^a*(?!a*$)b$`) is quadratic and must declare a window up to about 1000 (the loader accepts 720; 1024 is rejected); cubic shapes (`.*`
   twice before a scan, a lookaround over a repeat nested in another) fit only `window = 128`.

Guarantee: what is bounded is the number of steps of one `re.match` of a catalog regex on the
stripped `msg` (of at most the template's window, up to the first line break): at most 2.1 million
counted steps, whatever the degree of the polynomial: the degree only decides which window fits
(degree 1 at 4096, degree 2 at about 320 to 2300, degree 3 at 128). Calibration (empirical): the
model does not count constant factors, so the budget was set from the worst observed cost, about
19 ns per counted step (big classes and deep captures, now rejected by the item and depth limits),
against 0.8 to 5 ns on the catalog and the fuzz. Local measurements: accepted shapes at the budget
take about 20 to 40 ms; the budget is a safety factor, not a proof. The tests do not assume a
runner speed: they fail only at a catastrophe threshold of 0.25 s per probe (tolerant to a runner
5x slower). Every shipped regex is probed at its own window with inputs built from its own
literals and classes (child process with a timeout; worst about 4 ms locally), the reviewer
findings are kept as a regression table, and a fuzz of about 1000 accepted regexes (lookaround
alternations, nested lookarounds, optional alternations, Unicode classes, lazy and chained
bounded repeats; growing sizes, abort at 0.25 s) is probed the same way. The time per
line is that bound times the number of candidate templates tried. Not covered: `re.search`,
texts that are not stripped or that hold a line break, regexes used outside the catalog loader,
and other machines' speed (the bound is in steps; the time is measured). The model
over-approximates: some safe regexes are rejected or need a smaller window.

### Continuation parsers (`cont`)

Templates look only at the first line; a named parser extracts fields from the continuation lines.
Regex fields win over continuation fields with the same name. Parsers read at most 256 KiB.

| Parser | Options | Fields | Use |
|---|---|---|---|
| `codes_per_line` | `max_items` (32), `max_lines` (64) | `codes` (list) | `Detected errors:` + one code per line |
| `stats_csv` | `max_rows` (default: the 256 KiB continuation cap) | `rows` (`[method, calls, req_bytes, resp_bytes]`), `n_rows`, `rows_cut` | `[Communication2] - By call count:` sections |
| `dotnet_stack` | `max_lines` (400) | `exc_type`, `exc_msg`, `frame`, `build`, `inner_type` | .NET stack traces, `Reason:` / `Stack trace:` blocks |
| `kv_tab` | none | `items` | map dumps (`\t<key> - <value>`): counts, never keeps the pairs |
| `kv_colon` | `max_items` (32) | `kv` (table), `n_items` | `Key: value` lines; stops at the first line starting with `<` |
| `kv_dash` | `max_items` (32) | `kv`, `n_items` | `Key - value` lines; stops at `<` |
| `line_regex` | `regex` (required), `line` (1..20, default 1) | the regex groups | fields on a given continuation line (`Callback received:` + `<Type> GID: <gid>, ...`) |

Field types in `fields` also apply to `line_regex` outputs.

### Payload (`payload`)

| Key | Values | Meaning |
|---|---|---|
| `fmt` | `xml`, `xml_seq`, `html`, `json`, `text` | Content format (`xml_seq`: sibling elements without one root). |
| `start` | `match_end`, `msg`, `first_cont_lt` | Right after the regex match on the first line; the whole first line; the first continuation line starting with `<`. The payload runs to the end of the record. |
| `strip_suffix` | 1..8 characters | Removed from the end (the `.` the adapters append to request/response). |
| `role` | `request`, `response`, `fault`, `delta`, `content`, `publication`, `other`, `by_root` | `by_root`: decided by the root element (`...ResponseMessage` -> response, `...Fault` -> fault, else request). |
| `direction`, `peer` | `in`/`out`, identifier | Override `[payload_defaults]`. |

## Validation and examples

Loading raises a typed `TemplateError` subclass naming the file and the template:
`SchemaError` (keys, types, values), `DuplicateError` (codes, aliases, texts, families),
`InvalidRegexError` / `UnsafeRegexError`, `TemplateReferenceError` (`extends`, rule codes).

Regexes are checked as described in "Regex safety" above.

`Catalog.check_examples()` (and `load_catalog(check_examples=True)`, used by the tests) checks that
each positive example is classified as its template (so a template shadowed by an earlier one is
reported) with the declared fields, that no counter-example is, and that no example of a file
also matches another template of the same file.
