"""Authenticode assessment.

On Windows, ``WinVerifyTrust`` (WINTRUST_ACTION_GENERIC_VERIFY_V2) is called through ctypes
with revocation checking against the whole chain; offline revocation failures are reported
as "valid, revocation unchecked" instead of "valid". The signer name is taken from the
certificate table the verified signature belongs to and compared with the catalogue's
expected publisher(s).

Elsewhere no trust decision is possible: the result is ``UNAVAILABLE`` with the embedded
certificate names for information. A SHA-256 identifies content; it never proves origin.
"""
from __future__ import annotations

import ctypes
import sys
from dataclasses import dataclass
from pathlib import Path

from ..catalogue.pe import PEFormatError, inspect_pe
from ..domain.enums import SignatureState
from ..storage.db import utcnow

TRUST_E_NOSIGNATURE = 0x800B0100
TRUST_E_SUBJECT_FORM_UNKNOWN = 0x800B0003
TRUST_E_PROVIDER_UNKNOWN = 0x800B0001
CERT_E_REVOCATION_FAILURE = 0x800B010E
CRYPT_E_REVOCATION_OFFLINE = 0x80092013
CRYPT_E_NO_REVOCATION_CHECK = 0x80092012
TRUST_E_EXPLICIT_DISTRUST = 0x800B0111
TRUST_E_BAD_DIGEST = 0x80096010
CERT_E_UNTRUSTEDROOT = 0x800B0109
CERT_E_EXPIRED = 0x800B0101


@dataclass
class SignatureAssessment:
    state: SignatureState
    publisher: str | None
    detail: str
    checked_utc: str

    @property
    def label(self) -> str:
        return {
            SignatureState.VALID: f"Valid signature ({self.publisher})",
            SignatureState.VALID_REVOCATION_UNCHECKED: f"Valid signature, revocation not checked ({self.publisher})",
            SignatureState.UNEXPECTED_PUBLISHER: f"Valid signature from an unexpected publisher ({self.publisher})",
            SignatureState.UNSIGNED: "Unsigned",
            SignatureState.INVALID: "Invalid signature",
            SignatureState.UNAVAILABLE: "Signature not verified on this platform",
            SignatureState.NOT_CHECKED: "Not checked",
        }[self.state]


def _leaf_name(cns: list[str], expected: tuple[str, ...]) -> str | None:
    for e in expected:
        if e in cns:
            return e
    skip = ("Sectigo", "USERTrust", "AAA Certificate", "DigiCert", "Microsoft", "Time Stamping", "Root", " CA ")
    for cn in cns:
        if not any(s in cn for s in skip):
            return cn
    return None


def assess_signature(path: str | Path, expected_signers: tuple[str, ...] = ()) -> SignatureAssessment:
    path = Path(path)
    now = utcnow()
    try:
        info = inspect_pe(path)
    except (OSError, PEFormatError) as exc:
        return SignatureAssessment(SignatureState.INVALID, None, f"Not a readable PE file: {exc}", now)
    if not info.has_certificate_table:
        return SignatureAssessment(SignatureState.UNSIGNED, None, "No Authenticode certificate table.", now)
    name = _leaf_name(info.certificate_common_names, expected_signers)
    if sys.platform != "win32":
        return SignatureAssessment(
            SignatureState.UNAVAILABLE, name,
            "Certificate table present (embedded names: " + ", ".join(info.certificate_common_names[:6]) + "). "
            "Authenticode trust can only be verified on Windows.", now)
    code = _win_verify_trust(path)
    if code == 0:
        if expected_signers and name not in expected_signers:
            return SignatureAssessment(SignatureState.UNEXPECTED_PUBLISHER, name, f"WinVerifyTrust succeeded; expected {', '.join(expected_signers)}.", now)
        return SignatureAssessment(SignatureState.VALID, name, "WinVerifyTrust: trusted chain, revocation checked.", now)
    if code in (CERT_E_REVOCATION_FAILURE, CRYPT_E_REVOCATION_OFFLINE, CRYPT_E_NO_REVOCATION_CHECK):
        code2 = _win_verify_trust(path, revocation=False)
        if code2 == 0:
            st = SignatureState.UNEXPECTED_PUBLISHER if expected_signers and name not in expected_signers else SignatureState.VALID_REVOCATION_UNCHECKED
            return SignatureAssessment(st, name, f"Chain trusted; revocation status unavailable (0x{code:08X}, likely offline).", now)
        code = code2
    if code == TRUST_E_NOSIGNATURE:
        return SignatureAssessment(SignatureState.UNSIGNED, None, "WinVerifyTrust: no signature.", now)
    if code in (TRUST_E_PROVIDER_UNKNOWN, TRUST_E_SUBJECT_FORM_UNKNOWN):
        return SignatureAssessment(SignatureState.UNAVAILABLE, name, f"WinVerifyTrust could not evaluate the file (0x{code:08X}).", now)
    return SignatureAssessment(SignatureState.INVALID, name, f"WinVerifyTrust failed with 0x{code:08X}.", now)


def _win_verify_trust(path: Path, revocation: bool = True) -> int:  # pragma: no cover - Windows only
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    class WINTRUST_FILE_INFO(ctypes.Structure):
        _fields_ = [("cbStruct", wintypes.DWORD), ("pcwszFilePath", wintypes.LPCWSTR), ("hFile", wintypes.HANDLE), ("pgKnownSubject", ctypes.c_void_p)]

    class WINTRUST_DATA(ctypes.Structure):
        _fields_ = [
            ("cbStruct", wintypes.DWORD), ("pPolicyCallbackData", ctypes.c_void_p), ("pSIPClientData", ctypes.c_void_p),
            ("dwUIChoice", wintypes.DWORD), ("fdwRevocationChecks", wintypes.DWORD), ("dwUnionChoice", wintypes.DWORD),
            ("pFile", ctypes.POINTER(WINTRUST_FILE_INFO)), ("dwStateAction", wintypes.DWORD), ("hWVTStateData", wintypes.HANDLE),
            ("pwszURLReference", wintypes.LPCWSTR), ("dwProvFlags", wintypes.DWORD), ("dwUIContext", wintypes.DWORD),
            ("pSignatureSettings", ctypes.c_void_p),
        ]

    action = GUID(0xAAC56B, 0xCD44, 0x11D0, (ctypes.c_ubyte * 8)(0x8C, 0xC2, 0x00, 0xC0, 0x4F, 0xC2, 0x95, 0xEE))
    fi = WINTRUST_FILE_INFO(ctypes.sizeof(WINTRUST_FILE_INFO), str(path), None, None)
    WTD_UI_NONE, WTD_REVOKE_NONE, WTD_REVOKE_WHOLECHAIN, WTD_CHOICE_FILE = 2, 0, 1, 1
    WTD_STATEACTION_VERIFY, WTD_STATEACTION_CLOSE = 1, 2
    WTD_REVOCATION_CHECK_CHAIN, WTD_CACHE_ONLY_URL_RETRIEVAL = 0x40, 0x1000
    data = WINTRUST_DATA()
    data.cbStruct = ctypes.sizeof(WINTRUST_DATA)
    data.dwUIChoice = WTD_UI_NONE
    data.fdwRevocationChecks = WTD_REVOKE_WHOLECHAIN if revocation else WTD_REVOKE_NONE
    data.dwUnionChoice = WTD_CHOICE_FILE
    data.pFile = ctypes.pointer(fi)
    data.dwStateAction = WTD_STATEACTION_VERIFY
    data.dwProvFlags = WTD_REVOCATION_CHECK_CHAIN if revocation else WTD_CACHE_ONLY_URL_RETRIEVAL
    wvt = ctypes.windll.wintrust.WinVerifyTrust
    wvt.argtypes = [wintypes.HWND, ctypes.POINTER(GUID), ctypes.POINTER(WINTRUST_DATA)]
    wvt.restype = ctypes.c_long
    rc = wvt(None, ctypes.byref(action), ctypes.byref(data)) & 0xFFFFFFFF
    data.dwStateAction = WTD_STATEACTION_CLOSE
    wvt(None, ctypes.byref(action), ctypes.byref(data))
    return rc
