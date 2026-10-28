"""Artifact type vocabulary with investigator-facing labels and tooltips.

Tooltips state what an artifact can and cannot show; they are used across Evidence,
Workflows, Results and Timeline so that presence artifacts are never presented as proof of
execution."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ArtifactType:
    id: str
    label: str
    tooltip: str
    is_dir: bool = False


_TYPES = [
    ArtifactType("prefetch_file", "Prefetch file", "Windows Prefetch (.pf). Records a program's executable name, run count and recent run times on systems with Prefetch enabled."),
    ArtifactType("prefetch_directory", "Prefetch folder", "Folder containing Prefetch (.pf) files.", True),
    ArtifactType("evtx_file", "Event log", "Windows event log (.evtx). Records are written by the providers that log to that channel."),
    ArtifactType("evtx_directory", "Event log folder", "Folder containing .evtx files.", True),
    ArtifactType("system_hive", "SYSTEM hive", "SYSTEM registry hive. Contains ShimCache (AppCompatCache), services, devices and control sets."),
    ArtifactType("software_hive", "SOFTWARE hive", "SOFTWARE registry hive. Optional companion for SRUM to resolve application and network names."),
    ArtifactType("sam_hive", "SAM hive", "SAM registry hive (local accounts)."),
    ArtifactType("security_hive", "SECURITY hive", "SECURITY registry hive."),
    ArtifactType("ntuser_hive", "NTUSER.DAT", "Per-user registry hive. Holds user-specific settings, MRUs and UserAssist."),
    ArtifactType("usrclass_hive", "UsrClass.dat", "Per-user classes hive. Holds most ShellBags on Windows 7 and later."),
    ArtifactType("amcache_hive", "Amcache.hve", "Amcache inventory hive. Shows files and programs that were present or inventoried; an entry is not by itself proof of execution."),
    ArtifactType("amcache_folder", "Amcache folder", "C:\\Windows\\AppCompat\\Programs folder (Amcache.hve and Inventory*.db).", True),
    ArtifactType("registry_hive", "Registry hive", "Registry hive of undetermined type."),
    ArtifactType("registry_transaction_log", "Hive transaction log", "Registry transaction log (.LOG1/.LOG2). Replayed by RLA or by tools that support logs; keep it with its hive."),
    ArtifactType("registry_hive_directory", "Registry hives folder", "Folder containing registry hives (and their transaction logs).", True),
    ArtifactType("ntfs_mft", "$MFT", "NTFS Master File Table. Timestamps are file system metadata and can be modified; they are not evidence of execution."),
    ArtifactType("ntfs_usnjrnl_j", "$UsnJrnl:$J", "NTFS change journal ($J). Records file system changes; parent paths need the matching $MFT from the same volume."),
    ArtifactType("ntfs_logfile", "$LogFile", "NTFS transaction log."),
    ArtifactType("ntfs_boot", "$Boot", "NTFS boot sector."),
    ArtifactType("ntfs_sds", "$Secure:$SDS", "NTFS security descriptor stream."),
    ArtifactType("ntfs_i30", "$I30", "NTFS directory index."),
    ArtifactType("lnk_file", "Shortcut (.lnk)", "Windows shortcut file. Its own timestamps and the embedded target timestamps have different meanings."),
    ArtifactType("lnk_directory", "Shortcuts folder", "Folder containing .lnk files.", True),
    ArtifactType("jumplist_file", "Jump List", "Automatic or custom destinations Jump List."),
    ArtifactType("jumplist_directory", "Jump Lists folder", "Folder containing Jump Lists.", True),
    ArtifactType("recyclebin_i_file", "Recycle Bin $I", "Recycle Bin metadata ($I file) with original path, size and deletion time."),
    ArtifactType("recyclebin_info2", "Recycle Bin INFO2", "Legacy (XP era) Recycle Bin index."),
    ArtifactType("recyclebin_directory", "Recycle Bin folder", "$Recycle.Bin folder.", True),
    ArtifactType("recentfilecache_bcf", "RecentFileCache.bcf", "Windows 7 era cache of recently created program paths; presence is not proof of execution."),
    ArtifactType("srum_db", "SRUM database", "SRUDB.dat. Resource usage by application/network interface over time; network bytes alone do not prove data exfiltration."),
    ArtifactType("sum_db", "User Access Log database", "Windows Server User Access Logging (SUM) database."),
    ArtifactType("sum_directory", "User Access Log folder", "Folder with SystemIdentity.mdb, Current.mdb and {GUID}.mdb files.", True),
    ArtifactType("activitiescache_db", "Windows Timeline database", "ActivitiesCache.db (Windows 10/11 Timeline)."),
    ArtifactType("sqlite_db", "SQLite database", "SQLite database (browser, application or OS). Keep -wal/-journal sidecars with it."),
    ArtifactType("sqlite_sidecar", "SQLite sidecar", "SQLite -wal, -shm or -journal file belonging to a database."),
    ArtifactType("sqlite_directory", "SQLite databases folder", "Folder searched for SQLite databases.", True),
    ArtifactType("iis_log", "IIS log", "IIS W3C log file."),
    ArtifactType("iis_log_directory", "IIS logs folder", "Folder containing IIS logs.", True),
    ArtifactType("shim_sdb", "Shim database (.sdb)", "Application compatibility shim database."),
    ArtifactType("csv_output", "CSV output", "CSV produced by a parser."),
    ArtifactType("any_file", "File", "Any file (for strings search or viewing)."),
    ArtifactType("any_directory", "Folder", "Any folder.", True),
    ArtifactType("collection_root", "Collection folder", "Root of a collected evidence tree (for example a KAPE target output).", True),
    ArtifactType("live_volume", "Live volume", "A mounted live volume. Live operations are separate from collected-evidence analysis."),
    ArtifactType("mounted_evidence", "Mounted evidence", "An already mounted evidence volume."),
]

ARTIFACT_TYPES: dict[str, ArtifactType] = {t.id: t for t in _TYPES}

HIVE_TYPES = ("system_hive", "software_hive", "sam_hive", "security_hive", "ntuser_hive", "usrclass_hive", "amcache_hive", "registry_hive")

# artifact type of a single file -> folder type used when grouping for directory-mode tools
DIRECTORY_OF = {
    "prefetch_file": "prefetch_directory",
    "evtx_file": "evtx_directory",
    "lnk_file": "lnk_directory",
    "jumplist_file": "jumplist_directory",
    "recyclebin_i_file": "recyclebin_directory",
    "sum_db": "sum_directory",
    "iis_log": "iis_log_directory",
    "sqlite_db": "sqlite_directory",
}


def label(type_id: str | None) -> str:
    if not type_id:
        return "Unclassified"
    t = ARTIFACT_TYPES.get(type_id)
    return t.label if t else type_id


def tooltip(type_id: str | None) -> str:
    t = ARTIFACT_TYPES.get(type_id or "")
    return t.tooltip if t else ""
