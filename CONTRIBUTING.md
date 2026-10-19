# Contributing

Run the checks listed in the README before opening a pull request. Keep provider tests offline and mock Apify requests. Do not add a real token, seller export, database, or personal search history to a fixture.

For a parsing change, include a small synthetic listing that failed before the fix. Check both the intended result and a nearby counterexample: a payment versus a full price, an engine mileage versus chassis mileage, or a model mentioned in a trade request.

Keep raw observations intact. Add schema changes to both migration directories and describe whether the runtime supports them. PostgreSQL is a reference schema, not an active backend.

For UI changes, test a narrow mobile viewport, keyboard navigation, a long title, a missing photo, an empty result, and a server error. Use the sample dataset for screenshots and label it.
