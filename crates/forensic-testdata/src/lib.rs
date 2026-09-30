//! Access to the shared ForensicRS test artifacts from integration tests.
//!
//! Artifacts are downloaded and hash-verified by `forensic-testenv/tools/fetch.py`, which
//! records them in `<cache>/index.tsv`. This crate only reads that index: `cargo test`
//! never touches the network.
//!
//! ```no_run
//! #[test]
//! fn parses_security_log() {
//!     let path = forensic_testdata::artifact_or_skip!("evtx-security");
//!     let bytes = std::fs::read(path).unwrap();
//!     // ...
//! }
//! ```
//!
//! When an artifact is missing the test prints how to fetch it and passes (skip). With
//! `FORENSIC_TESTDATA_STRICT=1` (set in CI) a missing artifact fails the test instead.
//!
//! A *bundle* artifact (e.g. a triage collection) is a directory rather than a file. With the
//! `cases` feature, [`case_or_skip!`] gives the machines of a multi-machine scenario and the
//! facts its tests assert.

use std::path::PathBuf;

/// Directory holding the downloaded artifacts and `index.tsv`.
///
/// Must match `cache_dir()` in `forensic-testenv/tools/fetch.py`.
pub fn cache_dir() -> PathBuf {
    if let Some(dir) = std::env::var_os("FORENSIC_TESTDATA_DIR") {
        return PathBuf::from(dir);
    }
    if cfg!(windows) {
        let base = std::env::var_os("LOCALAPPDATA")
            .or_else(|| std::env::var_os("USERPROFILE"))
            .unwrap_or_default();
        return PathBuf::from(base).join("forensic-testdata");
    }
    let base = std::env::var_os("XDG_CACHE_HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            PathBuf::from(std::env::var_os("HOME").unwrap_or_default()).join(".cache")
        });
    base.join("forensic-testdata")
}

/// Whether missing artifacts must fail tests (`FORENSIC_TESTDATA_STRICT=1`).
pub fn strict() -> bool {
    matches!(
        std::env::var("FORENSIC_TESTDATA_STRICT").as_deref(),
        Ok("1") | Ok("true")
    )
}

/// Why an artifact is not usable.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Missing {
    /// `fetch.py` has not downloaded it (or the index does not exist yet).
    NotFetched,
    /// It is in the index but the file is gone or its size changed since it was verified.
    Stale(PathBuf),
}

#[cfg(feature = "cases")]
mod cases;
#[cfg(feature = "cases")]
pub use cases::{case, Case, Fact, Machine};

/// Looks up an artifact by its manifest id without printing or panicking. For a bundle this is
/// the directory it was extracted to.
pub fn lookup(id: &str) -> Result<PathBuf, Missing> {
    let index =
        std::fs::read_to_string(cache_dir().join("index.tsv")).map_err(|_| Missing::NotFetched)?;
    let (size, path) = index
        .lines()
        .filter_map(|line| {
            let mut cols = line.splitn(3, '\t');
            Some((cols.next()?, cols.next()?, cols.next()?))
        })
        .find(|(row_id, _, _)| *row_id == id)
        .map(|(_, size, path)| (size.to_owned(), PathBuf::from(path)))
        .ok_or(Missing::NotFetched)?;
    // Bundles are directories, listed with size "-"; fetch.py checked every file in them.
    match (std::fs::metadata(&path), size.as_str()) {
        (Ok(meta), "-") if meta.is_dir() => Ok(path),
        (Ok(meta), size) if meta.is_file() && size.parse::<u64>().ok() == Some(meta.len()) => {
            Ok(path)
        }
        _ => Err(Missing::Stale(path)),
    }
}

/// Returns the path of a fetched artifact, or `None` (after printing a skip notice) when
/// it is not available. Panics instead when [`strict`] mode is on.
pub fn artifact(id: &str) -> Option<PathBuf> {
    match lookup(id) {
        Ok(path) => Some(path),
        Err(why) => {
            let msg = match why {
                Missing::NotFetched => format!("test artifact `{id}` has not been fetched"),
                Missing::Stale(p) => {
                    format!("test artifact `{id}` changed on disk ({})", p.display())
                }
            };
            skip_or_panic(&msg, &format!("--id {id}"));
            None
        }
    }
}

/// Prints a skip notice, or panics in [`strict`] mode.
fn skip_or_panic(msg: &str, fetch_args: &str) {
    let hint = format!("run: forensic-testenv/tools/fetch.py {fetch_args}");
    if strict() {
        panic!("{msg} and FORENSIC_TESTDATA_STRICT is set; {hint}");
    }
    eprintln!("SKIPPED: {msg}; {hint}");
}

/// Like [`artifact`] but always panics when the artifact is missing.
pub fn require(id: &str) -> PathBuf {
    lookup(id).unwrap_or_else(|why| {
        panic!("test artifact `{id}` unavailable ({why:?}); run: forensic-testenv/tools/fetch.py --id {id}")
    })
}

/// Evaluates to the artifact path, or returns from the enclosing test (skipping it) when
/// the artifact has not been fetched.
#[macro_export]
macro_rules! artifact_or_skip {
    ($id:expr) => {
        match $crate::artifact($id) {
            Some(path) => path,
            None => return,
        }
    };
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;

    // Tests mutate process-wide env vars.
    pub(crate) static ENV: Mutex<()> = Mutex::new(());

    pub(crate) fn with_cache<F: FnOnce(&std::path::Path)>(f: F) {
        let _guard = ENV.lock().unwrap_or_else(|e| e.into_inner());
        let dir = std::env::temp_dir().join(format!("forensic-testdata-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        std::env::set_var("FORENSIC_TESTDATA_DIR", &dir);
        std::env::remove_var("FORENSIC_TESTDATA_STRICT");
        f(&dir);
        std::env::remove_var("FORENSIC_TESTDATA_DIR");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn finds_indexed_artifact() {
        with_cache(|dir| {
            let file = dir.join("sample.bin");
            std::fs::write(&file, b"abcd").unwrap();
            std::fs::write(
                dir.join("index.tsv"),
                format!("sample\t4\t{}\n", file.display()),
            )
            .unwrap();
            assert_eq!(lookup("sample"), Ok(file.clone()));
            assert_eq!(artifact("sample"), Some(file));
            assert_eq!(lookup("other"), Err(Missing::NotFetched));
        });
    }

    #[test]
    fn size_change_is_stale() {
        with_cache(|dir| {
            let file = dir.join("sample.bin");
            std::fs::write(&file, b"abcdef").unwrap();
            std::fs::write(
                dir.join("index.tsv"),
                format!("sample\t4\t{}\n", file.display()),
            )
            .unwrap();
            assert_eq!(lookup("sample"), Err(Missing::Stale(file)));
        });
    }

    #[test]
    fn bundle_directory() {
        with_cache(|dir| {
            let bundle = dir.join("bundle");
            std::fs::create_dir_all(&bundle).unwrap();
            std::fs::write(
                dir.join("index.tsv"),
                format!(
                    "b\t-\t{}\nf\t-\t{}\n",
                    bundle.display(),
                    dir.join("index.tsv").display()
                ),
            )
            .unwrap();
            assert_eq!(lookup("b"), Ok(bundle));
            // "-" is only valid for a directory.
            assert!(matches!(lookup("f"), Err(Missing::Stale(_))));
        });
    }

    #[test]
    fn missing_is_skipped_unless_strict() {
        with_cache(|_| {
            assert_eq!(artifact("nope"), None);
            std::env::set_var("FORENSIC_TESTDATA_STRICT", "1");
            assert!(std::panic::catch_unwind(|| artifact("nope")).is_err());
        });
    }
}
