-- dmslog per-case SQLite schema, version 1 (docs/ARQUITETURA.md, section 5).
-- Applied by dmslog.store.db.migrate(); PRAGMA user_version is set by the migration, not here.
-- Time: ts is integer milliseconds of the log's LOCAL time, preserved as is (encoded as if UTC).

-- Case and origin -----------------------------------------------------------
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);          -- case_id, name, created_at, pack version, dmslog version

CREATE TABLE collection(                                       -- one ERS (zip), one Log folder or one loose file
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL CHECK(kind IN ('ers_zip','folder','file')),
  path TEXT NOT NULL,                                          -- absolute local path
  ers_guid TEXT, ers_ts TEXT,                                  -- from ERS_CL_<GUID>_<yyyymmddHHMMSS>_...
  ers_variant TEXT CHECK(ers_variant IN ('enterprise','sensitive')),
  ers_group INTEGER,                                           -- same value for the Enterprise/Sensitive pair of one collection
  machine TEXT, system TEXT, label TEXT,                       -- e.g. DSPMPDMZINT01 / mpdmz / user label
  clock_offset_ms INTEGER NOT NULL DEFAULT 0,                  -- always 0 in 1.0 (one ERS per case); reserved
  added_at TEXT NOT NULL);

CREATE TABLE stream(                                           -- logical flow: one process (family+PID+sensitivity) in a collection
  id INTEGER PRIMARY KEY,
  collection_group INTEGER NOT NULL,                           -- ers_group (or collection.id); joins normal and sensitive
  family TEXT NOT NULL,                                        -- AWFM, AONT, ..., BLTHOST, OASERR, SERVICE, UNKNOWN
                                                               -- (open: new families come from families.toml)
  process TEXT, pid INTEGER, account TEXT, service TEXT,       -- WfmAdapter, 37228, dnaApp_mpdmz, DMS_INTEGRATION
  sensitive INTEGER NOT NULL,
  derive_checkpoint INTEGER NOT NULL DEFAULT 0,                -- entry.id from which events are recomputed
  UNIQUE(collection_group, family, process, pid, sensitive));

CREATE TABLE source(                                           -- physical file (or zip member)
  id INTEGER PRIMARY KEY,
  collection_id INTEGER NOT NULL REFERENCES collection(id),
  stream_id INTEGER REFERENCES stream(id),
  member TEXT NOT NULL,                                        -- relative path (in the zip or folder), '/' separator
  file_name TEXT NOT NULL,
  rotation INTEGER,                                            -- NULL = current .log; 0,1,... = .log.N
  envelope TEXT NOT NULL,                                      -- adms | oaserr | plain
  size INTEGER, mtime REAL, zip_crc INTEGER,
  head_hash TEXT,                                              -- sha1 of the first 4 KiB (identity under rotation)
  indexed_bytes INTEGER NOT NULL DEFAULT 0,
  tail_hash TEXT,                                              -- sha1 of the 4 KiB before indexed_bytes
  first_ts INTEGER, last_ts INTEGER, n_entries INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'pending',                      -- pending|indexing|ready|error|skipped
  error TEXT,
  UNIQUE(collection_id, member));

-- Records -------------------------------------------------------------------
CREATE TABLE entry(                                            -- deliberately narrow: this is the table the grid scans
  id INTEGER PRIMARY KEY,                                      -- ascending in file order
  source_id INTEGER NOT NULL,
  line_no INTEGER NOT NULL, byte_off INTEGER NOT NULL, nlines INTEGER NOT NULL,
  ts INTEGER,                                                  -- ms; NULL only in 'plain' envelope
  pid INTEGER, tid INTEGER,                                    -- tid NULL when the line has no [TID=...]
  level INTEGER,                                               -- 1 FATAL .. 8 VERBOSE (envelope)
  sev INTEGER NOT NULL,                                        -- 1 critical, 2 error, 3 warning, 4 info, 5 noise (inferred)
  tpl_id INTEGER NOT NULL,                                     -- template.id; 0 = unknown
  event_id INTEGER,                                            -- operation/block the record belongs to (nullable)
  flags INTEGER NOT NULL DEFAULT 0,                            -- bits: 1 partial, 2 has_cont, 4 ref_sensitive, 8 startup,
                                                               --       16 elevated-level window, 32 ts out of order,
                                                               --       64 has_payload
  fields TEXT,                                                 -- compact JSON of extracted fields (dynamic columns)
  msg TEXT NOT NULL);                                          -- 1st line after the envelope, no CRLF (spaces preserved)
CREATE INDEX ix_entry_ts      ON entry(ts, id);
CREATE INDEX ix_entry_src_ts  ON entry(source_id, ts, id);
CREATE INDEX ix_entry_sev_ts  ON entry(sev, ts, id);
CREATE INDEX ix_entry_tpl_ts  ON entry(tpl_id, ts, id);
CREATE INDEX ix_entry_event   ON entry(event_id) WHERE event_id IS NOT NULL;
CREATE INDEX ix_entry_tid     ON entry(source_id, tid, id);

CREATE TABLE entry_body(entry_id INTEGER PRIMARY KEY, cont TEXT NOT NULL);  -- continuations, '\n' separator

CREATE VIEW entry_text AS
  SELECT e.id AS rowid, e.msg AS msg, substr(b.cont, 1, 65536) AS cont   -- per-record FTS limit (section 6.6)
  FROM entry e LEFT JOIN entry_body b ON b.entry_id = e.id
  WHERE e.tpl_id NOT IN (SELECT id FROM template WHERE fts = 0);         -- Verbose trace kept out of the FTS (section 6.6)
CREATE VIRTUAL TABLE entry_fts USING fts5(msg, cont, content='entry_text', content_rowid='rowid',
  tokenize="unicode61 remove_diacritics 2");

CREATE TABLE entry_key(                                        -- correlation keys extracted by the templates
  key_type TEXT NOT NULL,                                      -- msgid, request_id, gid, inc, mrid, errcode, exc_type, op, client_id
                                                               -- (also keys extracted from payloads, section 6.6)
  value TEXT NOT NULL,
  entry_id INTEGER NOT NULL,
  PRIMARY KEY(key_type, value, entry_id)) WITHOUT ROWID;
CREATE INDEX ix_key_entry ON entry_key(entry_id);

-- Templates -----------------------------------------------------------------
CREATE TABLE template(
  id INTEGER PRIMARY KEY,                                      -- 0 reserved for "unknown"
  code TEXT NOT NULL UNIQUE,                                   -- WFM-010, GEN-STA-02, ...
  family TEXT NOT NULL, class TEXT NOT NULL,                   -- start|end|req_resp|validation|error|statistics|startup|lifecycle|other
  sev INTEGER, title TEXT, pattern TEXT NOT NULL,
  fts INTEGER NOT NULL DEFAULT 1,                              -- 0 = records of this template stay out of the FTS
  def_version TEXT NOT NULL);                                  -- hash of the TOML that defined it
CREATE TABLE unknown_sig(                                      -- records without a template, grouped by signature
  family TEXT NOT NULL, signature TEXT NOT NULL,
  n INTEGER NOT NULL, first_entry_id INTEGER NOT NULL,
  PRIMARY KEY(family, signature)) WITHOUT ROWID;

-- Derived events ------------------------------------------------------------
CREATE TABLE event(
  id INTEGER PRIMARY KEY,
  stream_id INTEGER NOT NULL,
  kind TEXT NOT NULL,          -- operation|startup|stats_block|level_window|gap|restart|retry_seq|map_reload|exception|
                               -- sensitive_window|clock_jump|service_state (open text: new families may add kinds)
  name TEXT,                   -- operation (GetIncidents, Changed crews, SendWorks...) or subtype
  key TEXT,                    -- main msgId/request_id/GUID, when there is one
  tid INTEGER,
  start_entry_id INTEGER, end_entry_id INTEGER,
  ts_start INTEGER NOT NULL, ts_end INTEGER, duration_ms INTEGER,
  status TEXT,                 -- ok|business_error|fail|timeout|open|truncated
  sev INTEGER NOT NULL,
  n_entries INTEGER,
  detail TEXT);                -- JSON: error codes, exception, attempts, level from->to...
CREATE INDEX ix_event_kind ON event(kind, name, ts_start);
CREATE INDEX ix_event_stream ON event(stream_id, ts_start);
CREATE INDEX ix_event_status ON event(status, ts_start) WHERE status <> 'ok';

-- Correlation ---------------------------------------------------------------
CREATE TABLE link(
  a_entry_id INTEGER NOT NULL, b_entry_id INTEGER NOT NULL,
  kind TEXT NOT NULL,          -- normal_sensitive|callback|retry|response|same_key
  confidence INTEGER NOT NULL, -- 3 identical key, 2 exact ts+TID, 1 same second+TID/order, 0 window heuristic
  PRIMARY KEY(a_entry_id, b_entry_id, kind)) WITHOUT ROWID;
CREATE INDEX ix_link_b ON link(b_entry_id);

-- Payloads (format in docs/formato/payloads.md; section 6.6) ----------------------
CREATE TABLE payload(          -- the text is NOT duplicated: offsets into msg + '\n' + cont of the record
  id INTEGER PRIMARY KEY,
  entry_id INTEGER NOT NULL,   -- record holding the payload (sensitive, or normal: Wfm Content being sent to CRS/WMS)
  event_id INTEGER,            -- operation it belongs to (filled by derive/payloads.py)
  event_conf INTEGER,          -- confidence of the event link: 3 key, 2 TID inside the operation, 1 time
  ref_entry_id INTEGER,        -- normal record that announces the payload (GEN-MSG-01, WFM-112, ...), if any
  pair_id INTEGER,             -- paired payload (request <-> response/fault), if any
  role TEXT NOT NULL,          -- request|response|fault|delta|content|publication|other
  direction TEXT NOT NULL,     -- in (received by the adapter) | out (sent by the adapter)
  peer TEXT,                   -- other side, from the TOML: WFM, SAP_PI, CRS, IMS, WMS, DMS_PUBSUB
  operation TEXT,              -- operation name (from the event, the template or the root element)
  msg_id TEXT, corr_id TEXT,   -- Header/MessageID and Header/CorrelationID, when present
  fmt TEXT NOT NULL,           -- xml|xml_seq (several sibling elements, no root)|html|json|text
  root TEXT,                   -- 1st element, with prefix (e.g. ns0:SwitchingStepsResponseMessage, s:Fault)
  char_off INTEGER NOT NULL, char_len INTEGER NOT NULL,   -- without the trailing '.' the adapter appends
  well_formed INTEGER,         -- 1 ok, 0 malformed, NULL not checked
  truncated INTEGER NOT NULL DEFAULT 0);
CREATE INDEX ix_payload_entry ON payload(entry_id);
CREATE INDEX ix_payload_event ON payload(event_id) WHERE event_id IS NOT NULL;
CREATE INDEX ix_payload_op ON payload(operation, role);
CREATE INDEX ix_payload_msg ON payload(msg_id) WHERE msg_id IS NOT NULL;

-- Periodic statistics and aggregates ------------------------------------------
CREATE TABLE stat_sample(
  stream_id INTEGER NOT NULL, ts INTEGER NOT NULL,
  side TEXT NOT NULL,          -- client|service
  method TEXT NOT NULL,        -- '[TOTAL]' or Contract.Method
  calls INTEGER, req_bytes INTEGER, resp_bytes INTEGER,          -- cumulative since process start
  d_calls INTEGER, d_req_bytes INTEGER, d_resp_bytes INTEGER,     -- delta to the previous sample; NULL after a drop (restart)
  PRIMARY KEY(stream_id, side, method, ts)) WITHOUT ROWID;

CREATE TABLE bucket(           -- per-minute counts for the timeline and panels
  source_id INTEGER NOT NULL, minute INTEGER NOT NULL, sev INTEGER NOT NULL, tpl_id INTEGER NOT NULL,
  n INTEGER NOT NULL,
  PRIMARY KEY(source_id, minute, sev, tpl_id)) WITHOUT ROWID;

CREATE TABLE bucket_sev(       -- template-free rollup of bucket: exact fast path for the sev/source histograms
  source_id INTEGER NOT NULL, minute INTEGER NOT NULL, sev INTEGER NOT NULL,
  n INTEGER NOT NULL,
  PRIMARY KEY(source_id, minute, sev)) WITHOUT ROWID;
CREATE INDEX bucket_sev_minute ON bucket_sev(minute, sev, n);  -- covering: all-source histograms scan in order

-- Effective installation configuration (read from the ERS) ---------------------
CREATE TABLE config_param(                                     -- one row per (layer, key): every layer stays visible
  collection_group INTEGER NOT NULL, product TEXT NOT NULL,     -- AdapterWfm, ... (Name attribute of <Product>)
  scope TEXT NOT NULL,                                          -- registry | ws:<Operation> | err:<Interface>
  key_path TEXT NOT NULL DEFAULT '',                            -- '' or the <Key> name (list: CallbackNotificationContactTypeIds)
  name TEXT NOT NULL, value TEXT, source_file TEXT NOT NULL,
  layer INTEGER NOT NULL,                                       -- -1 = AdapterX.xml; N = suffix _N (section 7.2)
  layer_label TEXT,                                             -- '' | Product | Custom | ... (text after _N_)
  effective INTEGER NOT NULL,                                   -- 1 = value that holds after precedence
  status TEXT NOT NULL,                                         -- only|wins|overridden|ambiguous
  PRIMARY KEY(collection_group, product, scope, key_path, name, source_file)) WITHOUT ROWID;
CREATE TABLE config_observed(                                   -- value the process logged at startup
  stream_id INTEGER NOT NULL, name TEXT NOT NULL,               -- name as it appears in the log ([PathToWebServiceConfiguration])
  value TEXT, is_default INTEGER NOT NULL,                      -- 1 = line "[Configuration] [Default] [X]: [v]"
  entry_id INTEGER NOT NULL,
  PRIMARY KEY(stream_id, name, entry_id)) WITHOUT ROWID;
CREATE TABLE error_config(
  collection_group INTEGER NOT NULL, adapter TEXT NOT NULL, interface TEXT NOT NULL,
  adms_id TEXT NOT NULL, adms_severity TEXT, ext_code TEXT, ext_severity TEXT, reason TEXT, details TEXT,
  eventing_threshold TEXT, source_file TEXT NOT NULL,
  PRIMARY KEY(collection_group, adapter, interface, adms_id)) WITHOUT ROWID;

-- User data (survives reindexing) -----------------------------------------------
CREATE TABLE bookmark(
  id INTEGER PRIMARY KEY,
  collection_id INTEGER NOT NULL, member TEXT NOT NULL, line_no INTEGER NOT NULL,  -- stable locator
  entry_id INTEGER,                                                                -- cache, rebuilt on reindex
  color TEXT, note TEXT, created_at TEXT NOT NULL);
CREATE TABLE saved_view(id INTEGER PRIMARY KEY, name TEXT NOT NULL, filter TEXT NOT NULL, columns TEXT, created_at TEXT NOT NULL);
