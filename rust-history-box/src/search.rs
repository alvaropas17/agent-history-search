//! Read-only search of the existing history index. No indexing or refresh occurs here.
//! Offsets count Unicode scalar values, not UTF-8 bytes or grapheme clusters.
//! Expansion `context` is the total output budget (at most 6000 scalars).

use rusqlite::{Connection, OpenFlags, OptionalExtension, Row, params};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{collections::HashSet, path::Path, time::Duration};

const HOSTS: &[&str] = &["pi", "omp", "pocket", "opencode", "claude", "codex"];
const SCOPE: &str = "Indexed stored text; active branch not verified";
const MAX_CONTEXT: usize = 6000;
const MAX_OFFSET: usize = i32::MAX as usize;

// Materialize BM25 before applying a window function. SQLite does not allow the
// FTS auxiliary function to run in the window-function evaluation context.
const FTS_SQL: &str = "
WITH scored AS MATERIALIZED (
    SELECT d.id,d.host,d.sess,d.role,d.ts,d.text,bm25(docs_fts) AS score
    FROM docs_fts JOIN docs d ON d.id=docs_fts.rowid
    WHERE docs_fts MATCH ?1 AND (?2='all' OR d.host=?2)
      AND d.host IN ('pi','omp','pocket','opencode','claude','codex')
), ranked AS (
    SELECT *,ROW_NUMBER() OVER (PARTITION BY host,sess ORDER BY score,id) AS rn
    FROM scored
)
SELECT r.id,r.host,r.sess,r.role,r.ts,r.text,t.title
FROM ranked r LEFT JOIN titles t ON t.host=r.host AND t.sess=r.sess
WHERE r.rn=1 ORDER BY r.score,r.id LIMIT 21";

const SHORT_SQL: &str = "
WITH ranked AS (
    SELECT d.*,ROW_NUMBER() OVER (
        PARTITION BY d.host,d.sess ORDER BY d.ts DESC,d.id DESC
    ) AS rn
    FROM docs d
    WHERE instr(lower(d.text),lower(?1))>0 AND (?2='all' OR d.host=?2)
      AND d.host IN ('pi','omp','pocket','opencode','claude','codex')
)
SELECT r.id,r.host,r.sess,r.role,r.ts,r.text,t.title
FROM ranked r LEFT JOIN titles t ON t.host=r.host AND t.sess=r.sess
WHERE r.rn=1 ORDER BY r.ts DESC,r.id DESC LIMIT 21";

#[derive(Debug)]
struct Document {
    id: i64,
    host: String,
    session: String,
    role: String,
    timestamp: i64,
    body: String,
    title: String,
}

impl Document {
    fn read(row: &Row<'_>) -> rusqlite::Result<Self> {
        Ok(Self {
            id: row.get(0)?,
            host: row.get(1)?,
            session: row.get(2)?,
            role: row.get(3)?,
            timestamp: row.get(4)?,
            body: row.get(5)?,
            title: row.get::<_, Option<String>>(6)?.unwrap_or_default(),
        })
    }

    fn digest(&self) -> Result<String, String> {
        let identity = serde_json::to_vec(&(
            self.id,
            &self.host,
            &self.session,
            &self.role,
            self.timestamp,
            &self.body,
        ))
        .map_err(|e| e.to_string())?;
        Ok(format!("{:x}", Sha256::digest(identity)))
    }

    fn metadata(&self, reference: &str) -> Value {
        json!({
            "host": self.host,
            "session": self.session,
            "role": self.role,
            "timestamp": self.timestamp,
            "title": self.title,
            "ref": reference,
        })
    }
}

fn open_index(path: &Path) -> Result<Connection, String> {
    // Do not set CREATE, READ_WRITE, URI or immutable: existing WAL data must
    // remain visible, and a missing database must never be created.
    let conn = Connection::open_with_flags(
        path,
        OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )
    .map_err(|e| e.to_string())?;
    conn.busy_timeout(Duration::from_secs(3))
        .map_err(|e| e.to_string())?;
    conn.pragma_update(None, "query_only", true)
        .map_err(|e| e.to_string())?;
    // Keep message text, titles and FTS ranks in the same read snapshot.
    conn.execute_batch("BEGIN").map_err(|e| e.to_string())?;
    Ok(conn)
}

fn words_of(q: &str) -> Result<Vec<String>, String> {
    if q.chars().count() > 1600 {
        return Err("q must not exceed 1600 characters".into());
    }
    if q.trim().is_empty() {
        return Ok(Vec::new());
    }
    let mut words = Vec::new();
    let mut seen = HashSet::new();
    // Match the UI's whitespace-only splitting. Punctuation stays inside each
    // literal term: Ctrl+Alt+H must not become ctrl AND alt. Validate all terms,
    // including those beyond the eight-term selection limit.
    for word in q.split_whitespace() {
        let len = word.chars().count();
        if len > 200 {
            return Err("query words must not exceed 200 characters".into());
        }
        if len >= 3 {
            let word = word.to_lowercase();
            if word.chars().count() > 200 {
                return Err("normalized query words must not exceed 200 characters".into());
            }
            if words.len() < 8 && seen.insert(word.clone()) {
                words.push(word);
            }
        }
    }
    if words.is_empty() {
        let short = q.trim();
        if short.chars().count() > 200 {
            return Err("fallback query must not exceed 200 characters".into());
        }
        words.push(short.to_string());
    }
    Ok(words)
}

fn fts_literal(terms: &[String]) -> String {
    // Use the literal-phrase quoting from history_index._match, but combine
    // terms with AND. Operators, quotes and FTS column syntax stay literal.
    terms
        .iter()
        .map(|term| format!("\"{}\"", term.replace('"', "\"\"")))
        .collect::<Vec<_>>()
        .join(" AND ")
}

fn first_hit(body: &str, terms: &[String]) -> (usize, usize) {
    // Lowercasing can expand a scalar (for example U+0130). Map each folded
    // UTF-8 byte back to its original scalar instead of using folded offsets.
    let mut folded = String::new();
    let mut positions = Vec::new();
    for (index, ch) in body.chars().enumerate() {
        for lower in ch.to_lowercase() {
            folded.push(lower);
            positions.extend(std::iter::repeat_n(index, lower.len_utf8()));
        }
    }
    let mut best: Option<(usize, usize)> = None;
    for term in terms {
        let term = term.to_lowercase();
        if let Some(byte) = folded.find(&term) {
            let start = positions[byte];
            let end = positions[byte + term.len() - 1] + 1;
            if best.is_none_or(|(old, width)| start < old || (start == old && end - start > width)) {
                best = Some((start, end - start));
            }
        }
    }
    best.unwrap_or((0, 0))
}

fn window(body: &str, start: usize, end: usize, length: usize) -> Value {
    json!({
        "text": body.chars().skip(start).take(end - start).collect::<String>(),
        "start": start,
        "end": end,
        "text_length": length,
        "has_before": start > 0,
        "has_after": end < length,
        "next_offset": if end < length { Some(end) } else { None },
    })
}

fn with_window(mut metadata: Value, excerpt: Value) -> Value {
    metadata.as_object_mut().unwrap().extend(excerpt.as_object().unwrap().clone());
    metadata
}

fn search_response(results: Vec<Value>, limited: bool) -> Value {
    json!({
        "results": results,
        "limited": limited,
        "scope": SCOPE,
        "next_step": "Expand only relevant refs; use offset=next_offset to continue without repeating text.",
    })
}

/// Search the existing index without refresh. Return at most 20 sessions.
/// Long terms use literal trigram FTS AND and BM25; short fallback queries use
/// SQLite's literal instr/lower matching (ASCII case folding) and newest first.
pub fn search(path: &Path, q: &str, host: &str) -> Result<Value, String> {
    if host != "all" && !HOSTS.contains(&host) {
        return Err("unknown history host".into());
    }
    let terms = words_of(q)?;
    if terms.is_empty() {
        return Ok(search_response(Vec::new(), false));
    }
    let ranked = terms.iter().all(|term| term.chars().count() >= 3);
    let needle = if ranked { fts_literal(&terms) } else { terms[0].clone() };
    let conn = open_index(path)?;
    let mut stmt = conn.prepare(if ranked { FTS_SQL } else { SHORT_SQL })
        .map_err(|e| e.to_string())?;
    let documents = stmt.query_map(params![needle, host], Document::read)
        .map_err(|e| e.to_string())?
        .collect::<rusqlite::Result<Vec<_>>>()
        .map_err(|e| e.to_string())?;
    let limited = documents.len() > 20;
    let mut results = Vec::new();
    for doc in documents.into_iter().take(20) {
        if doc.id <= 0 {
            return Err("index contains a nonpositive document id".into());
        }
        let length = doc.body.chars().count();
        let (anchor, width) = first_hit(&doc.body, &terms);
        if anchor > MAX_OFFSET {
            return Err("match anchor exceeds the supported offset range".into());
        }
        let reference = format!("{}:{}:{}", doc.id, doc.digest()?, anchor);
        let start = anchor.saturating_sub(120);
        let end = anchor.saturating_add(width).saturating_add(120).min(length);
        results.push(with_window(doc.metadata(&reference), window(&doc.body, start, end, length)));
    }
    Ok(search_response(results, limited))
}

fn parse_reference(reference: &str) -> Result<(i64, &str, usize), String> {
    let invalid = || "malformed_reference: use an exact ref returned by search".to_string();
    if reference.len() > 110 {
        return Err(invalid());
    }
    let parts: Vec<_> = reference.split(':').collect();
    if parts.len() != 3 {
        return Err(invalid());
    }
    let (id, digest, anchor) = (parts[0], parts[1], parts[2]);
    let digits = |s: &str| !s.is_empty() && s.bytes().all(|b| b.is_ascii_digit());
    if !digits(id) || id.starts_with('0') || id.len() > 19
        || digest.len() != 64
        || !digest.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        || !digits(anchor) || (anchor.len() > 1 && anchor.starts_with('0'))
    {
        return Err(invalid());
    }
    let id: i64 = id.parse().map_err(|_| invalid())?;
    let anchor: usize = anchor.parse().map_err(|_| invalid())?;
    if anchor > MAX_OFFSET {
        return Err(invalid());
    }
    Ok((id, digest, anchor))
}

/// Expand an exact search reference. Reject stale rows and out-of-range offsets.
/// Without an offset, center the total `context` budget around the match anchor.
/// With an offset, emit the next `context` scalars from that exact position.
pub fn expand(
    path: &Path,
    reference: &str,
    context: usize,
    offset: Option<usize>,
) -> Result<Value, String> {
    if !(1..=MAX_CONTEXT).contains(&context) {
        return Err("context must be in 1..6000 characters".into());
    }
    if offset.is_some_and(|n| n > MAX_OFFSET) {
        return Err("offset exceeds the supported range".into());
    }
    let (id, digest, anchor) = parse_reference(reference)?;
    let conn = open_index(path)?;
    let doc = conn.query_row(
        "SELECT d.id,d.host,d.sess,d.role,d.ts,d.text,t.title FROM docs d
         LEFT JOIN titles t ON t.host=d.host AND t.sess=d.sess WHERE d.id=?1",
        params![id],
        Document::read,
    ).optional().map_err(|e| e.to_string())?
        .ok_or_else(|| "stale_reference: history index changed; search again".to_string())?;
    if doc.digest()? != digest || !HOSTS.contains(&doc.host.as_str()) {
        return Err("stale_reference: history index changed; search again".into());
    }
    let length = doc.body.chars().count();
    if (length == 0 && anchor != 0) || (length > 0 && anchor >= length) {
        return Err("malformed_reference: anchor is outside the message".into());
    }
    if offset.is_some_and(|n| n > length) {
        return Err("offset is outside the message".into());
    }
    let start = offset.unwrap_or_else(|| anchor.saturating_sub(context / 2));
    let end = start.saturating_add(context).min(length);
    let mut result = with_window(doc.metadata(reference), window(&doc.body, start, end, length));
    result["scope"] = json!(SCOPE);
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{fs, path::PathBuf, sync::atomic::{AtomicU64, Ordering}, time::SystemTime};

    static SERIAL: AtomicU64 = AtomicU64::new(0);

    struct Fixture { dir: PathBuf, path: PathBuf }

    impl Fixture {
        fn new() -> Self {
            let stamp = SystemTime::now().duration_since(SystemTime::UNIX_EPOCH).unwrap().as_nanos();
            let dir = std::env::temp_dir().join(format!(
                "history-box-search-{}-{stamp}-{}", std::process::id(), SERIAL.fetch_add(1, Ordering::Relaxed)
            ));
            fs::create_dir(&dir).unwrap();
            let fixture = Self { path: dir.join("fixture.sqlite"), dir };
            let db = fixture.db();
            db.execute_batch("
                CREATE TABLE docs(id INTEGER PRIMARY KEY,host TEXT,sess TEXT,role TEXT,ts INTEGER,text TEXT);
                CREATE TABLE titles(host TEXT,sess TEXT,title TEXT,PRIMARY KEY(host,sess));
                CREATE VIRTUAL TABLE docs_fts USING fts5(text,content='docs',content_rowid='id',tokenize='trigram case_sensitive 0');
                CREATE TRIGGER docs_ai AFTER INSERT ON docs BEGIN
                    INSERT INTO docs_fts(rowid,text) VALUES(new.id,new.text); END;
            ").unwrap();
            fixture
        }

        fn db(&self) -> Connection { Connection::open(&self.path).unwrap() }

        fn add(&self, id: i64, host: &str, session: &str, text: &str) {
            self.db().execute(
                "INSERT INTO docs VALUES(?1,?2,?3,'user',?4,?5)",
                params![id, host, session, id * 1000, text],
            ).unwrap();
        }

        fn reference(&self, q: &str) -> String {
            search(&self.path, q, "all").unwrap()["results"][0]["ref"].as_str().unwrap().to_owned()
        }
    }

    impl Drop for Fixture {
        fn drop(&mut self) { let _ = fs::remove_dir_all(&self.dir); }
    }

    #[test]
    fn splits_only_whitespace_keeps_literals_and_caps_at_eight() {
        assert_eq!(words_of("One ONE\tONE, zwei_3;\nÁrbol 12 xy three four five six seven eight nine").unwrap(),
            ["one", "one,", "zwei_3;", "árbol", "three", "four", "five", "six"]);
        assert_eq!(words_of("Ctrl+Alt+H\u{2003}Ctrl+Alt+H").unwrap(), ["ctrl+alt+h"]);
        assert_eq!(words_of(" x ").unwrap(), ["x"]);
        assert_eq!(words_of("a b").unwrap(), ["a b"]);
        assert_eq!(fts_literal(&["a\" OR x".into(), "b*".into()]), "\"a\"\" OR x\" AND \"b*\"");
    }

    #[test]
    fn empty_query_returns_empty_without_opening_index() {
        let f = Fixture::new();
        let missing = f.dir.join("missing.sqlite");
        for q in ["", " \t\n\u{2003}"] {
            assert!(words_of(q).unwrap().is_empty());
            let found = search(&missing, q, "all").unwrap();
            assert_eq!(found["results"], json!([]));
            assert_eq!(found["limited"], false);
        }
        assert!(!missing.exists());
        assert!(search(&missing, &" ".repeat(1601), "all").is_err());
    }

    #[test]
    fn punctuation_and_fts_metacharacters_remain_literal() {
        for query in ["Ctrl+Alt+H", "needle*", "text:needle", "NEAR(needle,haystack)",
            "needle\"OR\"haystack", "needle^haystack", "{text}:needle", "needle-haystack", "AND"] {
            let f = Fixture::new();
            f.add(1, "pi", "literal", &format!("literal {query} sample"));
            f.add(2, "pi", "decoy", "Ctrl Alt H needle needles haystack needlehaystack near text");
            let found = search(&f.path, query, "all").unwrap();
            assert_eq!(found["results"].as_array().unwrap().len(), 1, "{query}");
            assert_eq!(found["results"][0]["session"], "literal", "{query}");
        }
    }

    #[test]
    fn standalone_fts_operator_is_an_and_joined_literal_term() {
        let f = Fixture::new();
        f.add(1, "pi", "literal", "needle NOT haystack");
        f.add(2, "pi", "decoy", "needle haystack");
        let found = search(&f.path, "needle NOT haystack", "all").unwrap();
        assert_eq!(found["results"].as_array().unwrap().len(), 1);
        assert_eq!(found["results"][0]["session"], "literal");
    }

    #[test]
    fn validates_all_input_before_opening_index() {
        let f = Fixture::new();
        for q in ["a".repeat(201), "x ".repeat(801),
            format!("one two three four five six seven eight {}", "z".repeat(201))] {
            assert!(search(&f.path, &q, "pi").is_err(), "{q}");
        }
        assert!(search(&f.path, "needle", "pi,omp").is_err());
        assert!(search(&f.path, "needle", "pi' OR 1=1 --").is_err());
        assert!(words_of(&"é".repeat(200)).is_ok());
    }

    #[test]
    fn and_bm25_session_deduplication_and_titles() {
        let f = Fixture::new();
        f.add(1, "pi", "same", "needle haystack");
        f.add(2, "pi", "same", "needle needle needle haystack haystack");
        f.add(3, "omp", "same", "needle haystack");
        f.add(4, "codex", "other", "needle alone");
        f.add(5, "unknown", "hidden", "needle haystack");
        f.db().execute("INSERT INTO titles VALUES('pi','same','Fixture title')", []).unwrap();
        let found = search(&f.path, "NEEDLE haystack", "all").unwrap();
        let results = found["results"].as_array().unwrap();
        assert_eq!(results.len(), 2);
        let expected: i64 = f.db().query_row(
            "SELECT d.id FROM docs_fts JOIN docs d ON d.id=docs_fts.rowid
             WHERE docs_fts MATCH ?1 AND d.host='pi' ORDER BY bm25(docs_fts),d.id LIMIT 1",
            ["\"needle\" AND \"haystack\""], |r| r.get(0),
        ).unwrap();
        let pi = results.iter().find(|r| r["host"] == "pi").unwrap();
        assert_eq!(pi["title"], "Fixture title");
        assert!(pi["ref"].as_str().unwrap().starts_with(&format!("{expected}:")));
        assert_eq!(search(&f.path, "needle haystack", "omp").unwrap()["results"].as_array().unwrap().len(), 1);
    }

    #[test]
    fn short_matching_is_literal_and_newest_first() {
        let f = Fixture::new();
        f.add(1, "pi", "one", "value % _ quote ' alpha x");
        f.add(2, "pi", "two", "plain alpha X");
        for term in ["%", "_", "'"] {
            let found = search(&f.path, term, "all").unwrap();
            assert_eq!(found["results"].as_array().unwrap().len(), 1);
            assert_eq!(found["results"][0]["session"], "one");
        }
        assert_eq!(search(&f.path, "x", "all").unwrap()["results"][0]["session"], "two");
    }

    #[test]
    fn quoted_short_fallback_is_not_an_fts_operator() {
        let f = Fixture::new();
        f.add(1, "pi", "literal", "literal \"x\" sample");
        f.add(2, "pi", "plain", "plain x sample");
        // The quotes are part of the three-scalar literal FTS term.
        let found = search(&f.path, "\"x\"", "all").unwrap();
        assert_eq!(found["results"].as_array().unwrap().len(), 1);
        assert_eq!(found["results"][0]["session"], "literal");
    }

    #[test]
    fn unicode_windows_and_nonrepeating_offsets() {
        let f = Fixture::new();
        let body = format!("{}needle{}", "🙂é".repeat(200), "終".repeat(7000));
        f.add(1, "pocket", "unicode", &body);
        let reference = f.reference("needle");
        assert!(reference.ends_with(":400"));
        let first = expand(&f.path, &reference, 400, None).unwrap();
        assert_eq!(first["start"], 200);
        assert_eq!(first["end"], 600);
        assert_eq!(first["text"].as_str().unwrap().chars().count(), 400);
        let next = first["next_offset"].as_u64().unwrap() as usize;
        let second = expand(&f.path, &reference, 1200, Some(next)).unwrap();
        assert_eq!(second["start"], first["end"]);
        assert_eq!(expand(&f.path, &reference, 6000, Some(0)).unwrap()["text"].as_str().unwrap().chars().count(), 6000);
        let length = body.chars().count();
        let end = expand(&f.path, &reference, 1, Some(length)).unwrap();
        assert_eq!(end["text"], "");
        assert_eq!(end["next_offset"], Value::Null);
        assert_eq!(end["has_after"], false);
        assert!(expand(&f.path, &reference, 10, Some(length + 1)).is_err());
    }

    #[test]
    fn folded_anchor_maps_back_to_original_scalars() {
        assert_eq!(first_hit("İ🙂prefix NEEDLE", &["needle".into()]), (9, 6));
        assert_eq!(first_hit("🙂İX", &["i\u{307}x".into()]), (1, 2));
    }

    #[test]
    fn rejects_stale_text_identity_and_deleted_rows() {
        let f = Fixture::new();
        f.add(1, "pi", "one", "needle original");
        let reference = f.reference("needle");
        let db = f.db();
        db.execute("UPDATE docs SET text='needle changed' WHERE id=1", []).unwrap();
        assert!(expand(&f.path, &reference, 400, None).unwrap_err().starts_with("stale_reference:"));
        db.execute("UPDATE docs SET text='needle original',sess='replacement' WHERE id=1", []).unwrap();
        assert!(expand(&f.path, &reference, 400, None).unwrap_err().starts_with("stale_reference:"));
        db.execute("DELETE FROM docs WHERE id=1", []).unwrap();
        assert!(expand(&f.path, &reference, 400, None).unwrap_err().starts_with("stale_reference:"));
    }

    #[test]
    fn rejects_malformed_refs_anchors_and_contexts() {
        let f = Fixture::new();
        f.add(1, "pi", "one", "needle");
        let reference = f.reference("needle");
        let digest = reference.split(':').nth(1).unwrap();
        for bad in ["".into(), "1:bad:0".into(), format!("0:{digest}:0"),
            format!("01:{digest}:0"), format!("-1:{digest}:0"),
            format!("1:{digest}:00"), format!("1:{digest}:6"),
            format!("1:{digest}:2147483648"), format!("1:{digest}:0:extra"),
            format!("9223372036854775808:{digest}:0"), "x".repeat(200)] {
            assert!(expand(&f.path, &bad, 400, None).is_err(), "{bad}");
        }
        for context in [0, 6001, usize::MAX] {
            assert!(expand(&f.path, &reference, context, None).is_err());
        }
        assert!(expand(&f.path, &reference, 400, Some(usize::MAX)).is_err());
    }

    #[test]
    fn limits_to_twenty_distinct_sessions() {
        let f = Fixture::new();
        for id in 1..=21 {
            f.add(id, "claude", &format!("session-{id}"), "needle");
        }
        let found = search(&f.path, "needle", "claude").unwrap();
        assert_eq!(found["results"].as_array().unwrap().len(), 20);
        assert_eq!(found["limited"], true);
        assert_eq!(search(&f.path, "absent", "all").unwrap()["limited"], false);
    }

    #[test]
    fn read_only_query_only_timeout_and_missing_file() {
        let f = Fixture::new();
        let conn = open_index(&f.path).unwrap();
        assert_eq!(conn.query_row("PRAGMA query_only", [], |r| r.get::<_, i64>(0)).unwrap(), 1);
        assert_eq!(conn.query_row("PRAGMA busy_timeout", [], |r| r.get::<_, i64>(0)).unwrap(), 3000);
        assert!(conn.execute("INSERT INTO titles VALUES('pi','x','x')", []).is_err());
        // Even disabling query_only cannot bypass SQLITE_OPEN_READ_ONLY.
        conn.pragma_update(None, "query_only", false).unwrap();
        assert!(conn.execute("INSERT INTO titles VALUES('pi','x','x')", []).is_err());
        let missing = f.dir.join("missing.sqlite");
        assert!(search(&missing, "needle", "all").is_err());
        assert!(!missing.exists());
    }
}
