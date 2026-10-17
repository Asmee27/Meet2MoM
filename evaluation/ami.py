"""
AMI Meeting Corpus loader (manual annotations v1.6.2, CC BY 4.0).

Turns the NXT XML into Meet2MoM's normalized form:
  - utterances: one per dialogue act (DA), ordered by start time, ids u00000...
    with speaker, times, text and the human DA label (Suggest, Offer, ...)
  - gold items: the "decisions" and "actions" sentences of the abstractive
    summary, each linked (via summlink) to the DAs it was written from. Those
    DAs are the gold evidence, so predicted claims can be scored by evidence
    overlap rather than by paraphrase similarity.
  - adjacency pairs: response -> proposal links typed POS/NEG/UNC/PART/ELA.

Source: https://groups.inf.ed.ac.uk/ami/download/ (Carletta et al., 2005).
Attribution required by CC BY 4.0; see eval_data/ami/LICENCE.txt after fetch.
"""
from __future__ import annotations

import html
import re
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
AMI_DIR = PROJECT_ROOT / "eval_data" / "ami"
AMI_ZIP = AMI_DIR / "ami_public_manual_1.6.2.zip"
AMI_URL = "https://groups.inf.ed.ac.uk/ami/AMICorpusAnnotations/ami_public_manual_1.6.2.zip"
AMI_SHA256 = "b56e5babb2496b8795deeeda7e71178d7fbc9963f94276cf2a3f4b56ebbc9f9d"

DA_LABELS = {
    "ami_da_1": "backchannel", "ami_da_2": "stall", "ami_da_3": "fragment",
    "ami_da_4": "inform", "ami_da_5": "elicit_inform", "ami_da_6": "suggest",
    "ami_da_7": "offer", "ami_da_8": "elicit_offer_suggestion", "ami_da_9": "assess",
    "ami_da_11": "elicit_assessment", "ami_da_12": "comment_understanding",
    "ami_da_13": "elicit_understanding", "ami_da_14": "be_positive",
    "ami_da_15": "be_negative", "ami_da_16": "other",
}
AP_TYPES = {"apt_1": "POS", "apt_2": "NEG", "apt_3": "UNC", "apt_4": "PART", "apt_5": "ELA"}

# Fixed BEFORE any modelling, by meeting series so whole teams are held out:
#   train: ES (and IB)  -> fit models
#   dev:   IS           -> model selection and threshold calibration
#   test:  TS           -> held out, scored only for the final report
SPLITS = {"train": ("ES", "IB"), "dev": ("IS",), "test": ("TS",)}


def split_of(meeting_id: str) -> str:
    for split, prefixes in SPLITS.items():
        if meeting_id.startswith(prefixes):
            return split
    return "unassigned"


@dataclass
class Utterance:
    utt_id: str
    da_id: str
    speaker: str
    start: float
    end: float
    text: str
    da_label: str


@dataclass
class GoldItem:
    item_id: str
    item_type: str              # "decision" | "action"
    text: str
    evidence: list[str]         # utt_ids of the linked DAs
    source: str = "ami_abstractive_summlink"


@dataclass
class Meeting:
    meeting_id: str
    split: str
    utterances: list[Utterance]
    gold: list[GoldItem]
    adjacency: list[tuple[str, str, str]] = field(default_factory=list)  # (source_utt, target_utt, type)


def fetch(force: bool = False) -> Path:
    """Download the annotation zip once (22.9 MB) and verify its checksum."""
    import hashlib

    AMI_DIR.mkdir(parents=True, exist_ok=True)
    if force or not AMI_ZIP.exists():
        urllib.request.urlretrieve(AMI_URL, AMI_ZIP)
    digest = hashlib.sha256(AMI_ZIP.read_bytes()).hexdigest()
    if digest != AMI_SHA256:
        raise RuntimeError(f"AMI zip checksum mismatch: {digest}")
    licence = AMI_DIR / "LICENCE.txt"
    if not licence.exists():
        with zipfile.ZipFile(AMI_ZIP) as z:
            licence.write_bytes(z.read("LICENCE.txt"))
    return AMI_ZIP


_ID_RANGE = re.compile(r"#id\(([^)]+)\)(?:\.\.id\(([^)]+)\))?")
_ELEM = re.compile(r"<(w|vocalsound)\b([^>]*?)(?:/>|>(.*?)</\1>)", re.S)
_ATTR = re.compile(r'([\w:]+)="([^"]*)"')


def _attrs(raw: str) -> dict:
    return dict(_ATTR.findall(raw))


class AMICorpus:
    def __init__(self, zip_path: Path = AMI_ZIP):
        self.zip = zipfile.ZipFile(zip_path)
        self._names = set(self.zip.namelist())

    def _read(self, name: str) -> str:
        return self.zip.read(name).decode("latin-1")

    def meeting_ids(self) -> list[str]:
        return sorted(
            n.split("/")[1].split(".")[0]
            for n in self._names
            if n.startswith("abstractive/") and n.endswith(".abssumm.xml")
            and f"extractive/{n.split('/')[1].split('.')[0]}.summlink.xml" in self._names
        )

    def _words(self, meeting_id: str, speaker: str):
        name = f"words/{meeting_id}.{speaker}.words.xml"
        if name not in self._names:
            return [], {}
        words = []
        for kind, raw_attrs, body in _ELEM.findall(self._read(name)):
            a = _attrs(raw_attrs)
            words.append({
                "id": a.get("nite:id"),
                "kind": kind,
                "text": html.unescape(body or "").strip(),
                "punc": a.get("punc") == "true",
                "start": float(a["starttime"]) if a.get("starttime") else None,
                "end": float(a["endtime"]) if a.get("endtime") else None,
            })
        return words, {w["id"]: i for i, w in enumerate(words)}

    def load(self, meeting_id: str) -> Meeting:
        das = []
        for speaker in "ABCDE":
            name = f"dialogueActs/{meeting_id}.{speaker}.dialog-act.xml"
            if name not in self._names:
                continue
            words, index = self._words(meeting_id, speaker)
            for block in re.findall(r"<dact\b(.*?)</dact>", self._read(name), re.S):
                da_id = re.search(r'nite:id="([^"]+)"', block).group(1)
                label_ref = re.search(r"da-types\.xml#id\((ami_da_\d+)\)", block)
                child = re.search(r'role?="?[^"]*"?\s*|', block)  # noqa: F841 (keep simple)
                span = re.search(r"<nite:child href=\"[^\"]*?" + r"#id\(([^)]+)\)(?:\.\.id\(([^)]+)\))?", block)
                if not span or span.group(1) not in index:
                    continue
                a = index[span.group(1)]
                b = index.get(span.group(2) or span.group(1), a)
                tokens, starts, ends = [], [], []
                for w in words[a : b + 1]:
                    if w["kind"] != "w" or not w["text"]:
                        continue
                    if w["punc"] and tokens:
                        tokens[-1] += w["text"]
                    else:
                        tokens.append(w["text"])
                    if w["start"] is not None:
                        starts.append(w["start"])
                    if w["end"] is not None:
                        ends.append(w["end"])
                text = " ".join(tokens).strip()
                if not text or not starts:
                    continue
                start = min(starts)
                end = max(ends) if ends else start + 0.01
                das.append({
                    "da_id": da_id, "speaker": speaker, "start": start,
                    "end": max(end, start + 0.01), "text": text,
                    "label": DA_LABELS.get(label_ref.group(1), "unlabelled") if label_ref else "unlabelled",
                })
        das.sort(key=lambda d: (d["start"], d["end"], d["speaker"]))
        utterances = [
            Utterance(f"u{i:05d}", d["da_id"], d["speaker"], d["start"], d["end"], d["text"], d["label"])
            for i, d in enumerate(das)
        ]
        by_da = {u.da_id: u.utt_id for u in utterances}

        # Gold: abstractive decisions/actions + summlink evidence
        abstractive = self._read(f"abstractive/{meeting_id}.abssumm.xml")
        sentences = {}
        for section, item_type in (("decisions", "decision"), ("actions", "action")):
            block = re.search(rf"<{section}\b[^>]*>(.*?)</{section}>", abstractive, re.S)
            if not block:
                continue
            for sid, text in re.findall(r'<sentence nite:id="([^"]+)"[^>]*>(.*?)</sentence>', block.group(1), re.S):
                sentences[sid] = (item_type, html.unescape(re.sub(r"\s+", " ", text)).strip())
        links: dict[str, list[str]] = {sid: [] for sid in sentences}
        for block in re.findall(r"<summlink\b(.*?)</summlink>", self._read(f"extractive/{meeting_id}.summlink.xml"), re.S):
            ext = re.search(r'role="extractive"\s+href="[^#]*#id\(([^)]+)\)"', block)
            ab = re.search(r'role="abstractive"\s+href="[^#]*#id\(([^)]+)\)"', block)
            if ext and ab and ab.group(1) in links and ext.group(1) in by_da:
                links[ab.group(1)].append(by_da[ext.group(1)])
        gold = [
            GoldItem(sid, item_type, text, sorted(set(links[sid])))
            for sid, (item_type, text) in sentences.items()
        ]

        adjacency = []
        ap_name = f"dialogueActs/{meeting_id}.adjacency-pairs.xml"
        if ap_name in self._names:
            for block in re.findall(r"<adjacency-pair\b(.*?)</adjacency-pair>", self._read(ap_name), re.S):
                t = re.search(r"ap-types\.xml#id\((apt_\d+)\)", block)
                s = re.search(r'role="source"\s+href="[^#]*#id\(([^)]+)\)"', block)
                g = re.search(r'role="target"\s+href="[^#]*#id\(([^)]+)\)"', block)
                if t and s and g and s.group(1) in by_da and g.group(1) in by_da:
                    adjacency.append((by_da[s.group(1)], by_da[g.group(1)], AP_TYPES.get(t.group(1), "OTHER")))

        return Meeting(meeting_id, split_of(meeting_id), utterances, gold, adjacency)
