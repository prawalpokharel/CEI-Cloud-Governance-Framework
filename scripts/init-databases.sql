-- Runs once, on first container start (docker-entrypoint-initdb.d).
--
-- Creates the second database the test suite requires. tests/test_db_schema.py
-- deletes every tenant, which cascades to clusters, users, and API keys, so it
-- refuses to run when TEST_DATABASE_URL matches DATABASE_URL. Creating both up
-- front means that guard never has to fire.

CREATE DATABASE cloudoptimizer_test;
