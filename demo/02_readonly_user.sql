-- A least-privilege account for QueryMind: it can read `shop` and nothing else.
-- Use the same pattern for your own databases.
CREATE USER IF NOT EXISTS 'qm_readonly'@'%' IDENTIFIED BY 'qm_readonly_pw';
GRANT SELECT ON shop.* TO 'qm_readonly'@'%';
FLUSH PRIVILEGES;
