"""Business logic that is independent of HTTP request handling.

Keeping this logic out of the view functions makes it reusable (CLI, tests,
future API) and keeps the views small and readable.
"""
