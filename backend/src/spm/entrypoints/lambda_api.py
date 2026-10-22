"""AWS Lambda handler for the HTTP API (API Gateway HTTP API → Mangum → FastAPI)."""

from mangum import Mangum

from spm.api.app import create_app

handler = Mangum(create_app(), lifespan="auto")
