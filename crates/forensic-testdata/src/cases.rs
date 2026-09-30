//! Multi-machine scenarios (`forensic-testenv/manifest/cases/<id>.toml`).
//!
//! `fetch.py --case <id>` downloads every artifact of a case and, once all of them are
//! verified, copies the case file to `<cache>/cases/<id>.toml`. This module reads that copy
//! and resolves each machine to its directory.
//!
//! ```no_run
//! #[test]
//! fn computer_names() {
//!     let case = forensic_testdata::case_or_skip!("unizar-bolas-cocido");
//!     for fact in case.facts("hive-value") {
//!         let hive = case.fact_path(fact);
//!         let key = fact.expect["key"].as_str().unwrap();
//!         // open `hive`, read `key`, compare with fact.expect["value"] ...
//!     }
//! }
//! ```

use std::path::{Path, PathBuf};

use serde::Deserialize;

use crate::{cache_dir, lookup, skip_or_panic, Missing};

/// One scenario: several machines and the facts known about them.
#[derive(Debug, Clone, Deserialize)]
pub struct Case {
    pub id: String,
    #[serde(default)]
    pub description: String,
    /// Scenario rules that tests may need (e.g. "10.0.0.0/8 counts as Kazakhstan").
    #[serde(default)]
    pub notes: Vec<String>,
    #[serde(rename = "machine")]
    pub machines: Vec<Machine>,
    #[serde(default, rename = "fact")]
    pub facts: Vec<Fact>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct Machine {
    pub name: String,
    #[serde(default)]
    pub role: String,
    /// Id of the (bundle) artifact holding this machine's data.
    pub artifact: String,
    /// Subdirectory of the artifact that is the machine's root, if not the artifact itself.
    #[serde(default)]
    pub root: Option<String>,
    /// Resolved when the case is loaded.
    #[serde(skip)]
    pub dir: PathBuf,
}

/// A value a tool independent of ForensicRS read from one of the machines.
#[derive(Debug, Clone, Deserialize)]
pub struct Fact {
    pub machine: String,
    /// What is asserted: `hive-value`, `evtx-count`, `prefetch-run`, ... The keys of `expect`
    /// depend on it (see forensic-testenv's README).
    pub kind: String,
    /// File the fact is about, relative to the machine's root.
    #[serde(default)]
    pub path: Option<String>,
    pub expect: toml::Table,
    pub verified_by: String,
}

impl Case {
    /// Loads a fetched case without printing or panicking.
    pub fn load(id: &str) -> Result<Case, Missing> {
        let text = std::fs::read_to_string(cache_dir().join("cases").join(format!("{id}.toml")))
            .map_err(|_| Missing::NotFetched)?;
        let mut case: Case = toml::from_str(&text)
            .unwrap_or_else(|e| panic!("case `{id}` in the cache does not parse: {e}"));
        for m in &mut case.machines {
            let base = lookup(&m.artifact)?;
            m.dir = match &m.root {
                Some(root) => base.join(root),
                None => base,
            };
            if !m.dir.is_dir() {
                return Err(Missing::Stale(m.dir.clone()));
            }
        }
        Ok(case)
    }

    /// The machine called `name`. Panics if the case has no such machine.
    pub fn machine(&self, name: &str) -> &Machine {
        self.machines
            .iter()
            .find(|m| m.name == name)
            .unwrap_or_else(|| panic!("case `{}` has no machine `{name}`", self.id))
    }

    /// The facts of one kind.
    pub fn facts<'a>(&'a self, kind: &'a str) -> impl Iterator<Item = &'a Fact> + 'a {
        self.facts.iter().filter(move |f| f.kind == kind)
    }

    /// Absolute path of the file a fact is about. Panics if the fact has no `path`.
    pub fn fact_path(&self, fact: &Fact) -> PathBuf {
        let rel = fact
            .path
            .as_deref()
            .unwrap_or_else(|| panic!("{} fact on {} has no path", fact.kind, fact.machine));
        self.machine(&fact.machine).path(rel)
    }
}

impl Machine {
    /// A file of this machine, from a path relative to its root (`/`-separated).
    pub fn path(&self, rel: impl AsRef<Path>) -> PathBuf {
        self.dir.join(rel)
    }
}

/// Returns a fetched case, or `None` (after printing a skip notice) when it or one of its
/// artifacts is not available. Panics instead when [`crate::strict`] mode is on.
pub fn case(id: &str) -> Option<Case> {
    match Case::load(id) {
        Ok(case) => Some(case),
        Err(why) => {
            let msg = match why {
                Missing::NotFetched => format!("test case `{id}` has not been fetched"),
                Missing::Stale(p) => format!("test case `{id}` changed on disk ({})", p.display()),
            };
            skip_or_panic(&msg, &format!("--case {id}"));
            None
        }
    }
}

/// Evaluates to the [`Case`], or returns from the enclosing test (skipping it) when the case
/// has not been fetched.
#[macro_export]
macro_rules! case_or_skip {
    ($id:expr) => {
        match $crate::case($id) {
            Some(case) => case,
            None => return,
        }
    };
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::tests::with_cache;

    const CASE: &str = r#"
id = "demo"
notes = ["10.0.0.0/8 is Kazakhstan"]
[[machine]]
name = "PC1"
artifact = "triage-pc1"
root = "PC1_2017"
[[fact]]
machine = "PC1"
kind = "hive-value"
path = "registry/SYSTEM"
expect = { key = 'Select', name = "Current", value = 1 }
verified_by = "test"
"#;

    #[test]
    fn loads_case_and_resolves_machines() {
        with_cache(|dir| {
            assert!(matches!(Case::load("demo"), Err(Missing::NotFetched)));
            let bundle = dir.join("bundle");
            std::fs::create_dir_all(bundle.join("PC1_2017/registry")).unwrap();
            std::fs::write(
                dir.join("index.tsv"),
                format!("triage-pc1\t-\t{}\n", bundle.display()),
            )
            .unwrap();
            std::fs::create_dir_all(dir.join("cases")).unwrap();
            std::fs::write(dir.join("cases/demo.toml"), CASE).unwrap();

            let case = case("demo").expect("case is fetched");
            assert_eq!(case.notes.len(), 1);
            assert_eq!(case.machine("PC1").dir, bundle.join("PC1_2017"));
            let fact = case.facts("hive-value").next().unwrap();
            assert_eq!(fact.expect["value"].as_integer(), Some(1));
            assert_eq!(
                case.fact_path(fact),
                bundle.join("PC1_2017").join("registry/SYSTEM")
            );
            assert_eq!(case.facts("evtx-count").count(), 0);
        });
    }

    #[test]
    fn missing_machine_dir_is_stale() {
        with_cache(|dir| {
            std::fs::create_dir_all(dir.join("bundle")).unwrap();
            std::fs::write(
                dir.join("index.tsv"),
                format!("triage-pc1\t-\t{}\n", dir.join("bundle").display()),
            )
            .unwrap();
            std::fs::create_dir_all(dir.join("cases")).unwrap();
            std::fs::write(dir.join("cases/demo.toml"), CASE).unwrap();
            assert!(matches!(Case::load("demo"), Err(Missing::Stale(_))));
            assert!(case("demo").is_none());
        });
    }
}
