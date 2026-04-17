from dotenv import load_dotenv

# Load .env file for local development.
# In GitHub Actions, secrets are injected as environment variables automatically
# so this has no effect in CI — it only activates when a .env file is present locally.
load_dotenv()

from services.pipeline import run_master_pipeline


def master_pipeline():
    run_master_pipeline()


if __name__ == "__main__":
    master_pipeline()
