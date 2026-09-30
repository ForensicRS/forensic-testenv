# Synthetic artifacts

Scripts that generate artifacts ourselves, with **known ground truth**: every value in the file is
also written to a `<name>.truth.json`, so tests can assert exact results instead of "doesn't crash".
Generated files contain no personal data and are always redistributable (MIT).

Output isn't byte-reproducible across tool versions (e.g. SQLite writes its library version into
the file header). So an artifact is generated **once**, registered and published, and from then on
everyone uses the published copy pinned by its SHA-256:

```sh
python3 generators/chrome_history.py                 # -> generators/out/chrome_history/History (+ truth json)
tools/add_artifact.py generators/out/chrome_history/History \
    --id sqlite-chrome-history-synthetic --format sqlite --source synthetic \
    --license MIT --redistributable --used-by frnsc-sqlite \
    --description "Chrome History DB with 5 known URLs/visits"
tools/add_artifact.py generators/out/chrome_history/History.truth.json \
    --id sqlite-chrome-history-synthetic-truth --format json --source synthetic \
    --license MIT --redistributable --used-by frnsc-sqlite
tools/publish_hf.py --id sqlite-chrome-history-synthetic --id sqlite-chrome-history-synthetic-truth --yes
```

Good candidates for more generators: registry hives written on a throwaway Windows VM with a
scripted set of keys, EVTX exported after a scripted set of actions (`wevtutil epl`), Prefetch
from running known binaries. Commit the script that produced them next to this README.
