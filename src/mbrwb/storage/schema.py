"""Versioned schemas for the three database files.

* ``app.sqlite`` (per user): tool installations, verification records, launch sessions,
  recent cases.
* ``case.sqlite`` (per case): case metadata, evidence, plans, jobs, outputs, bookmarks,
  findings, notes, reports, audit log.
* ``results.sqlite`` (per case): imported datasets (one row table per dataset), typed
  projections and timeline events. Kept separate so a long import transaction never blocks
  analyst writes such as bookmarks or finding edits.

Column names are documented in ARCHITECTURE.md. Never edit an applied migration; append one.
"""
from __future__ import annotations

APP_MIGRATIONS = [
    (
        1,
        "initial app schema",
        """
        CREATE TABLE installations(
            id TEXT PRIMARY KEY,
            tool_id TEXT NOT NULL,
            version TEXT,
            product_version TEXT,
            flavor TEXT,                -- net10 | net4 | script | unknown
            runtime_json TEXT,          -- RuntimeRequirement as JSON
            architecture TEXT,
            install_dir TEXT NOT NULL,
            executable TEXT NOT NULL,   -- absolute path of the program to run
            managed_assembly TEXT,      -- X.dll for framework-dependent CLI tools
            exe_sha256 TEXT NOT NULL,
            assembly_sha256 TEXT,
            signature_state TEXT NOT NULL DEFAULT 'not_checked',
            signature_detail TEXT,
            signature_checked_utc TEXT,
            source TEXT NOT NULL,       -- download | import_folder | import_archive | external_reference
            source_detail TEXT,         -- URL or original path
            archive_sha256 TEXT,
            installed_utc TEXT NOT NULL,
            selected INTEGER NOT NULL DEFAULT 0,
            retained INTEGER NOT NULL DEFAULT 1,
            dependencies_json TEXT,     -- maps/plugins/batch inventories with hashes
            notes TEXT
        );
        CREATE INDEX ix_inst_tool ON installations(tool_id, selected);
        CREATE TABLE verification_records(
            id TEXT PRIMARY KEY,
            tool_id TEXT NOT NULL,
            installation_id TEXT REFERENCES installations(id),
            exe_sha256 TEXT NOT NULL,
            version TEXT,
            method TEXT NOT NULL,       -- help_capture | static_strings
            profile_id TEXT,
            result TEXT NOT NULL,       -- compatible | incompatible | error
            detail_json TEXT,
            help_text TEXT,
            help_sha256 TEXT,
            checked_utc TEXT NOT NULL
        );
        CREATE TABLE launch_sessions(
            id TEXT PRIMARY KEY,
            case_id TEXT,
            case_root TEXT,
            tool_id TEXT NOT NULL,
            installation_id TEXT,
            executable TEXT NOT NULL,
            exe_sha256 TEXT,
            args_json TEXT NOT NULL,
            purpose TEXT,
            classification TEXT NOT NULL, -- gui_session | maintenance | utility
            started_utc TEXT NOT NULL,
            ended_utc TEXT,
            exit_code INTEGER,
            pid INTEGER
        );
        CREATE TABLE recent_cases(
            case_root TEXT PRIMARY KEY,
            case_id TEXT,
            title TEXT,
            last_opened_utc TEXT NOT NULL,
            pinned INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE favorites(tool_id TEXT PRIMARY KEY);
        CREATE TABLE upstream_checks(
            id TEXT PRIMARY KEY,
            checked_utc TEXT NOT NULL,
            source_url TEXT NOT NULL,
            page_sha256 TEXT,
            entries_json TEXT NOT NULL,
            notes TEXT
        );
        """,
    ),
]

CASE_MIGRATIONS = [
    (
        1,
        "initial case schema",
        """
        CREATE TABLE cases(
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            examiner TEXT,
            description TEXT,
            reference TEXT,
            display_tz TEXT NOT NULL DEFAULT 'UTC',
            tags TEXT,
            is_demo INTEGER NOT NULL DEFAULT 0,
            created_utc TEXT NOT NULL,
            updated_utc TEXT NOT NULL
        );
        CREATE TABLE evidence_roots(
            id TEXT PRIMARY KEY,
            case_id TEXT NOT NULL REFERENCES cases(id),
            path TEXT NOT NULL,
            kind TEXT NOT NULL,           -- file | folder
            mode TEXT NOT NULL,           -- in_place | working_copy
            working_path TEXT,
            label TEXT,
            host TEXT,
            registered_utc TEXT NOT NULL,
            files_inspected INTEGER DEFAULT 0,
            candidates INTEGER DEFAULT 0,
            unavailable INTEGER DEFAULT 0,
            excluded INTEGER DEFAULT 0,
            discovery_state TEXT NOT NULL DEFAULT 'pending',
            manifest_path TEXT
        );
        CREATE TABLE evidence(
            id TEXT PRIMARY KEY,
            case_id TEXT NOT NULL REFERENCES cases(id),
            root_id TEXT REFERENCES evidence_roots(id),
            version INTEGER NOT NULL DEFAULT 1,
            supersedes TEXT REFERENCES evidence(id),
            original_path TEXT NOT NULL,
            working_path TEXT,
            rel_path TEXT,
            artifact_type TEXT,
            artifact_basis TEXT,          -- signature | structure | filename | operator
            host TEXT,
            user_profile TEXT,
            volume TEXT,
            size INTEGER,
            mtime_utc TEXT,
            ctime_utc TEXT,
            atime_utc TEXT,
            imported_utc TEXT NOT NULL,
            hash_state TEXT NOT NULL DEFAULT 'pending',
            sha256 TEXT,
            hash_note TEXT,
            availability TEXT NOT NULL DEFAULT 'available',
            availability_checked_utc TEXT,
            note TEXT
        );
        CREATE INDEX ix_evidence_case ON evidence(case_id, artifact_type);
        CREATE INDEX ix_evidence_root ON evidence(root_id);
        CREATE TABLE evidence_relations(
            id TEXT PRIMARY KEY,
            parent_id TEXT NOT NULL REFERENCES evidence(id),
            child_id TEXT NOT NULL REFERENCES evidence(id),
            relation TEXT NOT NULL,        -- transaction_log | sqlite_sidecar | same_volume_mft | software_hive | working_copy | derived_hive
            basis TEXT NOT NULL,           -- auto | operator
            created_utc TEXT NOT NULL,
            UNIQUE(parent_id, child_id, relation)
        );
        CREATE TABLE workflow_plans(
            id TEXT PRIMARY KEY,
            case_id TEXT NOT NULL REFERENCES cases(id),
            preset_id TEXT NOT NULL,
            preset_version INTEGER NOT NULL,
            created_utc TEXT NOT NULL,
            dry_run INTEGER NOT NULL DEFAULT 0,
            snapshot_json TEXT NOT NULL,   -- immutable copy of the full plan
            omissions_json TEXT NOT NULL,
            summary TEXT
        );
        CREATE TABLE jobs(
            id TEXT PRIMARY KEY,
            case_id TEXT NOT NULL REFERENCES cases(id),
            plan_id TEXT REFERENCES workflow_plans(id),
            step_key TEXT,
            retry_of TEXT REFERENCES jobs(id),
            kind TEXT NOT NULL,            -- parser | maintenance | kape | specialist
            tool_id TEXT NOT NULL,
            title TEXT,
            installation_id TEXT,
            adapter_id TEXT,
            profile_id TEXT,
            verification TEXT,
            executable TEXT,
            exe_sha256 TEXT,
            tool_version TEXT,
            launcher_json TEXT,
            args_json TEXT NOT NULL,
            display_command TEXT,
            cwd TEXT,
            env_json TEXT,
            dependency_json TEXT,
            options_json TEXT,
            evidence_group TEXT,
            host TEXT,
            user_profile TEXT,
            job_dir TEXT NOT NULL,
            output_root TEXT NOT NULL,
            display_tz TEXT,
            exclusive INTEGER NOT NULL DEFAULT 0,
            timeout_s INTEGER,
            requires_elevation INTEGER NOT NULL DEFAULT 0,
            state TEXT NOT NULL,
            phase TEXT,
            created_utc TEXT NOT NULL,
            started_utc TEXT,
            ended_utc TEXT,
            exit_code INTEGER,
            parser_outcome TEXT,
            import_outcome TEXT,
            error_category TEXT,
            error_message TEXT,
            next_action TEXT,
            warnings_json TEXT,
            classification_json TEXT,
            pid INTEGER
        );
        CREATE INDEX ix_jobs_case ON jobs(case_id, state);
        CREATE TABLE job_inputs(
            id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL REFERENCES jobs(id),
            evidence_id TEXT REFERENCES evidence(id),
            role TEXT NOT NULL,
            path TEXT NOT NULL,
            size_at_submit INTEGER,
            sha256_at_submit TEXT,
            size_at_run INTEGER,
            checked_at_run_utc TEXT
        );
        CREATE INDEX ix_job_inputs_job ON job_inputs(job_id);
        CREATE TABLE outputs(
            id TEXT PRIMARY KEY,
            case_id TEXT NOT NULL REFERENCES cases(id),
            job_id TEXT REFERENCES jobs(id),
            origin TEXT NOT NULL,          -- job | external | gui_session
            rel_path TEXT,
            abs_path TEXT NOT NULL,
            kind TEXT NOT NULL,            -- csv | json | jsonl | xml | html | body | text | log | hive | binary
            role TEXT,                     -- adapter-defined output role (e.g. pecmd.timeline)
            size INTEGER,
            sha256 TEXT,
            completion TEXT NOT NULL,      -- complete | partial | quarantined
            import_state TEXT NOT NULL,    -- see ImportOutcome; not_applicable for logs and hives
            import_detail TEXT,
            dataset_ids_json TEXT,
            declared_tool TEXT,
            declared_version TEXT,
            parent_evidence_json TEXT,
            created_utc TEXT NOT NULL
        );
        CREATE INDEX ix_outputs_job ON outputs(job_id);
        CREATE TABLE bookmarks(
            id TEXT PRIMARY KEY,
            case_id TEXT NOT NULL REFERENCES cases(id),
            target_kind TEXT NOT NULL,     -- record | event
            dataset_id INTEGER,
            rec_id INTEGER,
            event_id INTEGER,
            note TEXT,
            tags TEXT,
            author TEXT,
            snapshot_json TEXT,
            created_utc TEXT NOT NULL,
            modified_utc TEXT NOT NULL
        );
        CREATE TABLE findings(
            id TEXT PRIMARY KEY,
            case_id TEXT NOT NULL REFERENCES cases(id),
            title TEXT NOT NULL,
            narrative TEXT,
            status TEXT NOT NULL,          -- draft | open | confirmed | refuted | closed
            severity TEXT,                 -- analyst-assigned, optional
            tags TEXT,
            author TEXT,
            version INTEGER NOT NULL DEFAULT 1,
            created_utc TEXT NOT NULL,
            modified_utc TEXT NOT NULL
        );
        CREATE TABLE finding_versions(
            finding_id TEXT NOT NULL REFERENCES findings(id),
            version INTEGER NOT NULL,
            snapshot_json TEXT NOT NULL,
            saved_utc TEXT NOT NULL,
            author TEXT,
            PRIMARY KEY(finding_id, version)
        );
        CREATE TABLE finding_links(
            id TEXT PRIMARY KEY,
            finding_id TEXT NOT NULL REFERENCES findings(id),
            target_kind TEXT NOT NULL,     -- record | event | evidence | output
            dataset_id INTEGER,
            rec_id INTEGER,
            event_id INTEGER,
            evidence_id TEXT,
            output_id TEXT,
            snapshot_json TEXT NOT NULL,   -- display snapshot at time of linking
            added_utc TEXT NOT NULL,
            author TEXT
        );
        CREATE TABLE notes(
            id TEXT PRIMARY KEY,
            case_id TEXT NOT NULL REFERENCES cases(id),
            target_kind TEXT NOT NULL,     -- case | evidence | job | record | event | finding
            target_id TEXT,
            text TEXT NOT NULL,
            author TEXT,
            created_utc TEXT NOT NULL,
            amends TEXT REFERENCES notes(id)
        );
        CREATE TABLE reports(
            id TEXT PRIMARY KEY,
            case_id TEXT NOT NULL REFERENCES cases(id),
            created_utc TEXT NOT NULL,
            format TEXT NOT NULL,
            path TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            timezone TEXT NOT NULL,
            options_json TEXT NOT NULL,
            finding_versions_json TEXT NOT NULL,
            redaction_json TEXT NOT NULL
        );
        CREATE TABLE report_drafts(
            case_id TEXT PRIMARY KEY REFERENCES cases(id),
            options_json TEXT NOT NULL,
            modified_utc TEXT NOT NULL
        );
        CREATE TABLE saved_views(
            id TEXT PRIMARY KEY,
            case_id TEXT NOT NULL REFERENCES cases(id),
            scope TEXT NOT NULL,           -- results | timeline
            name TEXT NOT NULL,
            view_json TEXT NOT NULL,
            created_utc TEXT NOT NULL
        );
        CREATE TABLE launch_sessions(
            id TEXT PRIMARY KEY,
            case_id TEXT NOT NULL REFERENCES cases(id),
            tool_id TEXT NOT NULL,
            executable TEXT NOT NULL,
            exe_sha256 TEXT,
            args_json TEXT NOT NULL,
            subject_path TEXT,
            started_utc TEXT NOT NULL,
            ended_utc TEXT,
            exit_code INTEGER,
            note TEXT
        );
        CREATE TABLE audit_log(
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_utc TEXT NOT NULL,
            actor TEXT,
            action TEXT NOT NULL,
            target TEXT,
            detail_json TEXT
        );
        """,
    ),
]

RESULTS_MIGRATIONS = [
    (
        1,
        "initial results schema",
        """
        CREATE TABLE datasets(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id TEXT NOT NULL,
            output_id TEXT,
            job_id TEXT,
            origin TEXT NOT NULL,             -- job | external
            tool_id TEXT,
            tool_version TEXT,
            name TEXT NOT NULL,
            source_path TEXT NOT NULL,
            source_sha256 TEXT,
            source_size INTEGER,
            source_format TEXT NOT NULL,      -- csv | jsonl | json
            schema_profile TEXT,              -- recognised output schema id, or NULL
            importer_version INTEGER NOT NULL,
            encoding TEXT,
            columns_json TEXT,
            host TEXT,
            user_profile TEXT,
            evidence_ids_json TEXT,
            state TEXT NOT NULL,              -- importing | complete | partial | failed | cancelled | interrupted
            rows_total INTEGER DEFAULT 0,
            rows_ok INTEGER DEFAULT 0,
            rows_adjusted INTEGER DEFAULT 0,
            rows_rejected INTEGER DEFAULT 0,
            rejected_samples_json TEXT,
            started_utc TEXT NOT NULL,
            completed_utc TEXT,
            error TEXT,
            row_table TEXT,
            search_index TEXT NOT NULL DEFAULT 'none',   -- none | building | ready | failed
            timeline_state TEXT NOT NULL DEFAULT 'none', -- none | mapped | unsupported_schema | no_mapping | failed
            timeline_mapping TEXT,
            timeline_events INTEGER DEFAULT 0,
            timeline_unresolved INTEGER DEFAULT 0,
            superseded_by INTEGER
        );
        CREATE INDEX ix_datasets_job ON datasets(job_id);
        CREATE TABLE timeline_events(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id TEXT NOT NULL,
            dataset_id INTEGER NOT NULL REFERENCES datasets(id),
            rec_id INTEGER NOT NULL,
            host TEXT,
            user_profile TEXT,
            job_id TEXT,
            tool_id TEXT,
            tool_version TEXT,
            source_output TEXT,
            field TEXT NOT NULL,
            raw_value TEXT NOT NULL,
            utc_ticks INTEGER,               -- FILETIME-style 100 ns ticks since 1601-01-01 UTC; NULL when unresolved
            precision_digits INTEGER,
            ts_status TEXT NOT NULL,
            ts_assumption TEXT,
            meaning TEXT NOT NULL,
            category TEXT,
            object_path TEXT,
            description TEXT,
            mapping_id TEXT NOT NULL,
            mapping_version INTEGER NOT NULL
        );
        CREATE INDEX ix_events_time ON timeline_events(utc_ticks);
        CREATE INDEX ix_events_rec ON timeline_events(dataset_id, rec_id);
        CREATE INDEX ix_events_status ON timeline_events(ts_status);
        """,
    ),
    (
        2,
        "drop low-selectivity timestamp status index (unresolved lookups use utc_ticks IS NULL)",
        """
        DROP INDEX IF EXISTS ix_events_status;
        """,
    ),
]
