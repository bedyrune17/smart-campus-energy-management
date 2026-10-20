"""Smart Campus Energy Management - shared backend package.

Everything in here is pure Python (stdlib + boto3, which the Lambda runtime
already ships), so the same code runs inside AWS Lambda, in the local
simulator, in the unit tests and in the demo-data generator.
"""

__version__ = "1.0.0"
