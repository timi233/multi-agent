#!/bin/sh
# Pi platform - S1 PostgreSQL 角色预留（手册 §4.1 第 2 条）
# 说明：本文件不是生产迁移（手册规则：禁止服务启动时隐式执行生产迁移）；
#       仅创建按唯一写入者划分的最小角色集（表格尚未创建，授权留待实现仓库迁移）。
# 实验配置：svc_* 角色统一使用部署时注入的 SVC_ROLES_PASSWORD。
# 注意：psql 变量替换不发生在 dollar-quoted 块内，故密码在 shell 层转义后内联。
set -eu

: "${POSTGRES_USER:?required}"
: "${SVC_ROLES_PASSWORD:?required}"
: "${POSTGRES_DB:=pi_platform}"

# SQL 单引号转义
pw_sq=$(printf '%s' "$SVC_ROLES_PASSWORD" | sed "s/'/''/g")
export pw_sq

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<SQL
DO \$\$
DECLARE
  r TEXT;
  pw TEXT := '$pw_sq';
  roles TEXT[] := ARRAY[
    'svc_task_api',      -- task_specs / task_admission_decisions / idempotency_records(scope=task)
    'svc_lifecycle',     -- tasks.state / runs / required_run_bindings / task_cancel_requests / terminal_report_grants / attempts(平台收敛状态)
    'svc_attempt',       -- attempts(CLAIMED..TERMINAL_REPORTED/FAILED_PROVISIONING) / attempt_contracts
    'svc_lease',         -- leases / execution_assignments / resource_execution_epochs
    'svc_artifact',      -- artifact_manifests / artifact_references
    'svc_delivery',      -- commit_intents / candidate_staging_operations / candidate_authorization_bindings
    'svc_delivery_authz',-- git_staging_leases / delivery_authorizations
    'svc_revocation',    -- revocation_records / revocation_view_checkpoints / revocation_check_attestations
    'svc_ledger',        -- budget_grants / budget_ledger_entries / budget_uncertain_liabilities
    'svc_reconciler',    -- reconciliation_cases（只读其他）
    'svc_registry',      -- 各 *_snapshots / *_publications / policy_approval_*
    'svc_gateway'        -- Model Gateway（预留）
  ];
BEGIN
  FOREACH r IN ARRAY roles LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
      EXECUTE format('CREATE ROLE %I LOGIN PASSWORD %L', r, pw);
      RAISE NOTICE 'created role %', r;
    ELSE
      RAISE NOTICE 'role % already exists, skipped', r;
    END IF;
  END LOOP;
END \$\$;
SQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
REVOKE ALL ON DATABASE pi_platform FROM PUBLIC;
GRANT CONNECT ON DATABASE pi_platform TO svc_task_api, svc_lifecycle, svc_attempt, svc_lease,
    svc_artifact, svc_delivery, svc_delivery_authz, svc_revocation, svc_ledger, svc_reconciler,
    svc_registry, svc_gateway;
SELECT rolname, rolcanlogin FROM pg_roles WHERE rolname LIKE 'svc_%' ORDER BY 1;
SQL