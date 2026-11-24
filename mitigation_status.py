"""Read the Spectre/Meltdown mitigation state that the Windows kernel reports.

Uses NtQuerySystemInformation with the two information classes that
Microsoft's SpeculationControl PowerShell module also uses:
  196 = SystemKernelVaShadowInformation   (Meltdown / KVA shadow)
  201 = SystemSpeculationControlInformation (Spectre variant 2 / BTI)

Raw flag words are always saved, so the decoding below can be re-checked
against Microsoft's SpeculationControl module later.

Run on its own to print the current state:
    python mitigation_status.py
"""

import ctypes
import json
import sys
import winreg

STATUS_SUCCESS = 0
STATUS_INVALID_INFO_CLASS = 0xC0000003
STATUS_INFO_LENGTH_MISMATCH = 0xC0000004

CLASS_KVA_SHADOW = 196
CLASS_SPECULATION_CONTROL = 201

# Bit meanings as defined in Microsoft's SpeculationControl module.
KVA_FLAGS = {
    "kva_shadow_enabled": 0x01,
    "kva_shadow_user_global": 0x02,
    "kva_shadow_pcid": 0x04,
    "kva_shadow_invpcid": 0x08,
    "kva_shadow_required": 0x10,  # 1 = this CPU is vulnerable to Meltdown
    "kva_shadow_required_available": 0x20,
}

SPEC_FLAGS = {
    "bpb_enabled": 0x01,  # branch target injection (Spectre v2) mitigation active
    "bpb_disabled_system_policy": 0x02,  # turned off by policy (registry)
    "bpb_disabled_no_hardware_support": 0x04,  # no microcode support
    "spec_ctrl_enumerated": 0x08,
    "pred_cmd_enumerated": 0x10,
    "ibrs_present": 0x20,
    "stibp_present": 0x40,
    "smep_present": 0x80,
    "ssbd_available": 0x100,
    "ssbd_supported": 0x200,
    "ssbd_system_wide": 0x400,
    "ssbd_required": 0x1000,
    "retpoline_enabled": 0x4000,
    "import_optimization_enabled": 0x8000,
    "enhanced_ibrs": 0x10000,
    "mds_hardware_protected": 0x1000000,
    "mb_clear_enabled": 0x2000000,
}

REG_PATH = r"SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management"
REG_VALUES = ("FeatureSettingsOverride", "FeatureSettingsOverrideMask", "FeatureSettings")


def _query(info_class):
    """Return (status, flags_word) for one information class."""
    ntdll = ctypes.WinDLL("ntdll")
    for size in (8, 4):
        buf = ctypes.create_string_buffer(size)
        ret_len = ctypes.c_ulong(0)
        status = ntdll.NtQuerySystemInformation(
            ctypes.c_ulong(info_class), buf, ctypes.c_ulong(size), ctypes.byref(ret_len)
        )
        status &= 0xFFFFFFFF
        if status == STATUS_INFO_LENGTH_MISMATCH:
            continue
        if status != STATUS_SUCCESS:
            return status, None
        return status, int.from_bytes(buf.raw[:4], "little")
    return STATUS_INFO_LENGTH_MISMATCH, None


def _decode(word, table):
    if word is None:
        return None
    return {name: bool(word & bit) for name, bit in table.items()}


def _registry():
    out = {}
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, REG_PATH) as key:
            for name in REG_VALUES:
                try:
                    out[name] = winreg.QueryValueEx(key, name)[0]
                except FileNotFoundError:
                    out[name] = None
    except OSError as exc:
        out["error"] = str(exc)
    return out


def get_status():
    kva_status, kva_word = _query(CLASS_KVA_SHADOW)
    spec_status, spec_word = _query(CLASS_SPECULATION_CONTROL)
    kva = _decode(kva_word, KVA_FLAGS)
    spec = _decode(spec_word, SPEC_FLAGS)

    meltdown_on = bool(kva and kva["kva_shadow_enabled"])
    spectre_v2_on = bool(spec and spec["bpb_enabled"])
    if kva is None and spec is None:
        overall = "unknown"
    elif meltdown_on or spectre_v2_on:
        overall = "on"
    else:
        overall = "off"

    return {
        "overall": overall,
        "meltdown_kva_shadow_active": meltdown_on,
        "cpu_needs_meltdown_fix": bool(kva and kva["kva_shadow_required"]),
        "spectre_v2_mitigation_active": spectre_v2_on,
        "kva_raw": None if kva_word is None else hex(kva_word),
        "kva_query_status": hex(kva_status),
        "kva_flags": kva,
        "spec_raw": None if spec_word is None else hex(spec_word),
        "spec_query_status": hex(spec_status),
        "spec_flags": spec,
        "registry": _registry(),
    }


def short_summary(st):
    return (
        f"overall={st['overall']} | Meltdown fix (KVA shadow) active={st['meltdown_kva_shadow_active']}"
        f" (CPU needs it={st['cpu_needs_meltdown_fix']}) | Spectre v2 fix active="
        f"{st['spectre_v2_mitigation_active']} | raw kva={st['kva_raw']} spec={st['spec_raw']}"
    )


if __name__ == "__main__":
    if sys.platform != "win32":
        sys.exit("Windows only.")
    st = get_status()
    print(short_summary(st))
    print(json.dumps(st, indent=2))
