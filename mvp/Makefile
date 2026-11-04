.PHONY: install test sync sync-dry api clean

install:
	pip install -r requirements.txt

test:
	pytest -q

## full local run against the deterministic mock source
sync:
	DATABASE_URL=sqlite:///./cvm.db python -m jobs.weekly_sync --mock --seed-events

## evaluate everything, send nothing
sync-dry:
	DATABASE_URL=sqlite:///./cvm.db python -m jobs.weekly_sync --mock --dry-run --quiet

api:
	DATABASE_URL=sqlite:///./cvm.db uvicorn app.main:app --reload --port 8000

clean:
	rm -f *.db
	find . -name __pycache__ -type d -exec rm -rf {} +
