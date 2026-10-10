# local-samples

Local-only folder for real ERS zips and test aliases used by the sample-dependent tests.
Everything here is git-ignored except this README: never commit samples or credentials.
Optional `aliases.toml` maps real adapter spellings to neutral names (see README, "Local aliases").
Tests that need the zips skip cleanly when they are absent.
