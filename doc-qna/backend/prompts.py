# from typing import Any, Dict, List, Optional
# import re

# DENIAL_LINE = "I'm an equipment intelligence assistant. Please ask something related to laboratory, analytical, manufacturing, or utility equipment."

# BULLET_CHAR = "\u2022"


# EQUIPMENT_INTELLIGENCE_SYSTEM_PROMPT = """
# You are an equipment intelligence assistant for industrial and lab equipment manuals — operation, calibration, maintenance, qualification, troubleshooting, and Indian regulatory contexts.

# Grounding:
# - Use RETRIEVED CONTEXT as the source of truth. Do not invent procedures, specs, or details that aren't in context.
# - If a chunk is only a heading or image reference, say the manual provides it as a figure/image; don't describe unseen content.

# Format: Bullets for lists, headings for sections, prose for facts.


# """.strip()


# # Active response-format contract. The answer post-processor (postprocess_rag_answer,
# # normalize_runon_bullet_lines, ensure_blank_line_before_key_points) expects exactly this
# # shape — a lead paragraph, a blank line, a "Key points:" header, then one "• " bullet per
# # line. Keep this enabled so technical answers come out structured instead of one blob.
# RAG_ANSWER_FORMAT = """
# ANSWER FORMAT (for equipment/manual questions with RETRIEVED CONTEXT):
# - Start with ONE short paragraph (2-3 sentences) that answers the question directly, using only facts supported by the passages.
# - Then a completely blank line, then the header on its own line: **Key points:**
# - After the header, put one fact per line, each line starting with "• " (bullet + one space). Exactly one bullet per line — never chain facts with ";" or put two bullets on one line.
# - Use 3-8 bullets when the manual lists several rules, specs, or conditions; use fewer only if the context is genuinely narrow. Optional **bold** labels inside a bullet are fine.
# - For steps/procedures/how-to: keep every step in the manual's order as its own bullet (or numbered line); do not skip, merge, or summarize away distinct steps, warnings, or tools.
# - Do not repeat the user's question. No "Would you like to know...", no closing offers, no chunk/passage IDs.
# """.strip()


# #
# # RAG_ANSWER_FORMAT_AND_GROUNDING = """
# # TECHNICAL ANSWERS (when the  asks about equipment/manuals and RETRIEVED CONTEXT is provided below):
# # GROUNDING:
# # - Prefer exact values, units, limits, names, and step wording from RETRIEVED CONTEXT. Do not invent procedures, specs, or comparisons.
# # - If the context is insufficient, say so briefly. If passages conflict, favor the most specific excerpt (table, labeled spec, numbered procedure) and state uncertainty briefly if needed.
# # - For definition or "principles" questions, paraphrase the manual closely: use its stated relationships (if/when, before/after, stored vs immediate) rather than a generic textbook story.

# # EXTRACTIVE ANSWERS (reduce hallucination):
# # - Treat RETRIEVED CONTEXT as the source of truth: when a rule, condition, or procedure appears there, carry it into the answer with the manual's own wording whenever possible.
# # - Prefer reusing 1-3 consecutive sentences (or a tight clause) from a passage—optionally in quotation marks—over compressing them into a shorter paraphrase that drops qualifiers or invents new words.
# # - Each **Key points** bullet should map to a specific idea in at least one passage; use separate bullets for separate rules (do not merge unrelated constraints into one vague line).

# # ANTI-HALLUCINATION (strict):
# # - Do not add scientific benefits or motives (e.g. "enhance resolution", "sampling efficiency", "improve sensitivity") unless that benefit language appears in RETRIEVED CONTEXT.
# # - Do not assert workflow shapes ("sequentially", "one after another") unless the manual states that ordering explicitly.
# # - Do not add quantitative or consistency claims (e.g. "consistent sample volumes", "same volume") unless the manual states them.
# # - Do not invent mechanisms (e.g. "minimizing solvent transfer", "preventing complete parking") unless those phrases or equivalent steps appear in RETRIEVED CONTEXT. Prefer the manual's own terms (e.g. flushing, contamination, parking deck).

# # COMPLETENESS (principles / methods / "how it works"):
# # - Read all passages: if the manual states analysis order (e.g. reverse/backward order), the reason (e.g. contamination), loss conditions (e.g. all loops full), required intermediate steps (e.g. flush gradient before a new deck), or guarantees (e.g. each peak fully analysed before switching), include each as its own bullet when present. Do not omit constraints that appear in context for the sake of a shorter answer.

# # CHROMATOGRAPHY / MULTIDIMENSIONAL LC (when relevant):
# # - Use valve, loop, cut, deck, parking, modulation, etc. only when those concepts appear in RETRIEVED CONTEXT. Do not add extra instrument detail or comparisons (e.g. vs comprehensive 2D-LC) without support in the text.

# # CITATIONS (user-facing):
# # - Never include chunk numbers, passage IDs, or bracket tags like [Source: chunk N]. Optional: mention the document file name and/or page from the passage header once if it helps the reader; otherwise omit citations.

# # FORMAT:
# # - One short paragraph first (2-3 sentences) answering the question directly, using only supported facts from the passages.
# # - Put one completely blank line before the line **Key points:** (Markdown: end the paragraph, then a blank line, then the header).
# # - After **Key points:**, use one bullet per line: start each line with the character • (bullet), a single space, then exactly one fact; no semicolon chains and no multiple bullets on one line.
# # - Use 3-8 bullets when the manual lists several distinct rules or conditions; use fewer only if the context is genuinely narrow.
# # - Optional **bold** labels inside a bullet are fine. Prefer faithful excerpts over a single vague summary line.
# # - Do not repeat the user question. No "Would you like to know..." or similar closers.
# # """.strip()


# # METADATA_REFERENCE_INSTRUCTION = """
# # PASSAGE HEADERS:
# # Each excerpt is labeled with document name and, when available, page and section. Use these only for light traceability (e.g. document name or page). Do not expose internal retrieval labels in the answer.
# # """.strip()


# # CONVERSATIONAL_TONE_INSTRUCTION = """
# # TONE:
# # - Be warm and professional for greetings/thanks/acknowledgments.
# # - Be structured and precise for technical/equipment queries.
# # """.strip()




# metadata_reference_instruction = """
# **METADATA REFERENCE IN CONTEXT:**
# The retrieved context includes metadata with each text chunk. Most chunks come from documents
# ("Document: <name> | page <N>"), but video transcripts come from minute-long timeframes
# ("Document: <name> | timestamp <MM:SS-MM:SS>").

# When generating your response:
# - Use the document title, heading, and page number information to provide accurate source references for documents
# - For video transcripts, cite the matching timeframe in the same way you cite a PDF page, using the exact "MM:SS-MM:SS" string from the metadata
# - You can naturally reference the source document, section heading, page number, or video timestamp when relevant
# - PDF example: "According to Manual.pdf, page 12, in the Safety section..."
# - Video example: "As explained in AccessControl.mp4, 01:00-02:00, the visitor entry process requires..."
# - Always pair the timestamp with the video filename it came from, matching the pattern "<filename>.mp4, MM:SS-MM:SS" so the UI can turn it into a clickable link
# - Do NOT include the literal word "timestamp" in your answer; write only the filename and the time range, separated by a comma (e.g. write "AccessControl.mp4, 01:00-02:00", NOT "AccessControl.mp4, timestamp 01:00-02:00")
# - Do NOT invent timestamps that are not present in the retrieved metadata
# - Include these references naturally in your response when it adds value to the user's understanding
# """.strip()


# def _tokenize_words(text: str) -> List[str]:
#     out: List[str] = []
#     cur = []
#     for ch in text.lower():
#         if ch.isalnum():
#             cur.append(ch)
#         elif cur:
#             out.append("".join(cur))
#             cur = []
#     if cur:
#         out.append("".join(cur))
#     return out




# def build_system_prompt() -> str:
#     parts = [
#         EQUIPMENT_INTELLIGENCE_SYSTEM_PROMPT,
#         RAG_ANSWER_FORMAT,
#         # RAG_ANSWER_FORMAT_AND_GROUNDING,
#         # METADATA_REFERENCE_INSTRUCTION,
#         # CONVERSATIONAL_TONE_INSTRUCTION,
#         # RAG_REFERENCES_OUTPUT_INSTRUCTION,
#         metadata_reference_instruction,
#     ]
#     return "\n\n".join(parts).strip()


# def build_rag_system_prompt() -> str:
#     """System / instruction block only (shared across RAG turns; safe to KV-cache as a prefix)."""
#     return build_system_prompt()


# def _serialize_context(context_chunks: List[Dict[str, Any]]) -> str:
#     context_strs: List[str] = []
#     for i, ch in enumerate(context_chunks, start=1):
#         doc = ch.get("doc_name", "unknown")
#         text = ch.get("text", "")
#         section = (ch.get("section_path_str") or "").strip()
#         page = ch.get("page_number", "")
#         timestamp = (ch.get("timestamp") or "").strip()
#         is_video = (
#             str(ch.get("chunk_type") or "").strip() == "video_segment"
#             or str(ch.get("ingest_mode") or "").strip() == "video_transcript"
#             or bool(timestamp)
#         )
#         head_parts = [f"Document: {doc}"]
#         if is_video and timestamp:
#             head_parts.append(f"timestamp {timestamp}")
#         else:
#             if page not in ("", "?", None):
#                 head_parts.append(f"page {page}")
#             if section:
#                 head_parts.append(f"section: {section}")
#         header = " | ".join(head_parts)
#         context_strs.append(f"Passage [{i}] — {header}\n{text.strip()}")
#     return "\n\n".join(context_strs)


# def build_rag_retrieved_context_block(context_chunks: List[Dict[str, Any]]) -> str:
#     """Serialized retrieved passages only (variable per query)."""
#     return _serialize_context(context_chunks)


# def build_rag_prompt_static() -> str:
#     """
#     Fixed prefix: `build_rag_system_prompt()` plus the ``USER QUESTION:`` header.

#     Keep this identical across requests so llama-cpp-python ``LlamaRAMCache`` can
#     match the longest shared token prefix and reuse KV for the static system block
#     when only the dynamic tail (question + ``RETRIEVED CONTEXT``) changes.
#     """
#     return build_rag_system_prompt() + "\n\nUSER QUESTION:\n"


# def build_rag_prompt_dynamic(
#     question: str,
#     context_chunks: List[Dict[str, Any]],
#     *,
#     batch_index: Optional[int] = None,
#     batch_count: Optional[int] = None,
#     previous_answer_draft: Optional[str] = None,
#     query_language: Optional[str] = None,
# ) -> str:
#     """Variable tail: user question, retrieved context block, and answer cue."""
#     context_block = build_rag_retrieved_context_block(context_chunks)
#     lang = (query_language or "").strip()
#     lang_block = ""
#     if lang:
#         if lang.lower() == "hinglish":
#             lang_block = (
#                 "\n\nRESPONSE LANGUAGE:\n"
#                 "The user's query is Hinglish/Romanized Hindi. The final answer MUST be in Hinglish using Roman script only.\n"
#                 "- Use Hindi-style sentence structure in English letters, for example: \"JDZ-120 manual ke according...\", "
#                 "\"motor power rating ... hai\", \"power supply specification ... hai\".\n"
#                 "- Keep English technical terms, model names, units, dimensions, document titles, page/section references unchanged.\n"
#                 "- Do NOT answer in pure English.\n"
#                 "- Do NOT use Devanagari script/Hindi letters.\n"
#             )
#         elif lang.lower() == "hindi":
#             lang_block = (
#                 "\n\nRESPONSE LANGUAGE:\n"
#                 "The user's query is Hindi/Devanagari. Write the final answer in Hindi, while keeping English "
#                 "technical terms, model names, units, dimensions, and document references unchanged when needed.\n"
#             )
#         elif lang.lower() == "english":
#             lang_block = (
#                 "\n\nRESPONSE LANGUAGE:\n"
#                 "The user's query is English. Write the final answer in English.\n"
#             )
#     prev = (previous_answer_draft or "").strip()
#     prev_block = ""
#     if prev:
#         prev_block = (
#             "\n\nPREVIOUS ANSWER DRAFT (from earlier batch):\n"
#             f"{prev}\n"
#             "\n(Use this only for structure continuity."
#             "Only append net-new facts supported by this batch. Continue numbering/format if the same section continues.)"
#             "**Never repeat or rephrase the previous answer.**\n"
#         )
#     note = ""
#     if (
#         batch_index is not None
#         and batch_count is not None
#         and int(batch_count) > 1
#     ):
#         bi = int(batch_index)
#         bc = int(batch_count)
#         if bi <= 1:
#             note = (
#                 f"\n\n(NOTE: Excerpts are split across {bc} parts; "
#                 f"this is part {bi} of {bc}. "
#                 "Answer using ONLY the passages below; extract facts from this part; "
#                 "avoid a long generic introduction.)\n"
#             )
#         else:
#             note = (
#                 f"\n\n(NOTE: Excerpts are split across {bc} parts; "
#                 f"this is part {bi} of {bc}. "
#                 "**Never repeat or rephrase the PREVIOUS ANSWER DRAFT.** "
#                 "Use ONLY this part as evidence. If this part does not directly add new facts "
#                 "that answer the user question, output nothing (empty response; zero characters). "
#                 "Do not write 'not found', 'not provided', 'does not mention', "
#                 "'document does not provide', or any disclaimer/fallback sentence. "
#                 "No headings, no preface, no summary, and no reference lines when direct evidence is absent.)\n"
#             )
#     return f"""{question}{note}{lang_block}{prev_block}

# RETRIEVED CONTEXT:
# {context_block}

# FINAL ANSWER:
# """


# def build_rag_prompt_parts(
#     question: str,
#     context_chunks: List[Dict[str, Any]],
# ) -> tuple[str, str]:
#     return build_rag_prompt_static(), build_rag_prompt_dynamic(question, context_chunks)


# def build_rag_prompt(
#     question: str,
#     context_chunks: List[Dict[str, Any]],
# ) -> str:
#     static, dynamic = build_rag_prompt_parts(question, context_chunks)
#     return static + dynamic


# def _split_line_multiple_bullets(line: str) -> str:
#     s = line.rstrip()
#     if s.lower().startswith("http"):
#         return line
#     b = BULLET_CHAR
#     if s.count(b) <= 1:
#         return line
#     parts = re.split(rf"\s+{re.escape(b)}\s+", s)
#     if len(parts) <= 1:
#         return line
#     first, rest = parts[0].strip(), [p.strip() for p in parts[1:] if p.strip()]
#     if not rest:
#         return line
#     if first.startswith(b):
#         body = [first] + [f"{b} {p}" if not p.startswith(b) else p for p in rest]
#     else:
#         body = [first] + [f"{b} {p}" for p in rest]
#     return "\n".join(body)


# def normalize_runon_bullet_lines(text: str) -> str:
#     """
#     Fix common model layout mistakes: chained bullets after ; or sentence end, or multiple • on one line.
#     """
#     t = (text or "").strip()
#     if not t:
#         return t
#     b = BULLET_CHAR
#     be = re.escape(b)
#     nb = "\n\n" + b + " "
#     t = re.sub(rf";\s*{be}\s*", nb, t)
#     t = re.sub(r";\s*-\s+", nb, t)
#     t = re.sub(rf"([.?!])\s+{be}\s*", lambda m: m.group(1) + nb, t)
#     t = re.sub(r"([.?!])\s+-\s+(?=[A-Z])", lambda m: m.group(1) + nb, t)
#     t = re.sub(rf"(:\s*){be}\s*(?=\S)", lambda m: m.group(1) + nb, t)
#     lines = [_split_line_multiple_bullets(ln) for ln in t.splitlines()]
#     t = "\n".join(lines)
#     t = re.sub(r"\n{3,}", "\n\n", t).strip()
#     return t


# def ensure_blank_line_before_key_points(text: str) -> str:
#     """Force 'Key points:' onto its own line with a blank line before it, and push any bullet
#     the model glued onto the header line down to the next line.

#     Handles the variants the small model emits: bold ('**Key points:**'), inline (glued to the
#     end of the paragraph after a sentence), and a first bullet stuck right after the header.
#     """
#     t = text or ""
#     # Canonicalize the header (optional ** and surrounding spaces) to: blank line, bold header,
#     # newline. The trailing newline breaks off any "• ..." the model glued onto the same line.
#     t = re.sub(
#         r"[ \t]*(?:\*\*)?[ \t]*key\s*points?\s*:?[ \t]*(?:\*\*)?[ \t]*",
#         "\n\n**Key points:**\n",
#         t,
#         count=1,
#         flags=re.I,
#     )
#     t = re.sub(r"\n{3,}", "\n\n", t)
#     return t.strip()


# _TRAIL_FILLER_LINES = frozenset(
#     {
#         "hi",
#         "hello",
#         "hey",
#         "ok",
#         "okay",
#         "thanks",
#         "thank you",
#         "thankyou",
#     }
# )


# def strip_internal_chunk_citations(text: str) -> str:
#     """Remove model echoes of chunk/Source tags (not user-verifiable)."""
#     t = text or ""
#     t = re.sub(r"\s*\[Source:\s*chunk[^\]]+\]", "", t, flags=re.I)
#     t = re.sub(r"\s*\(Source:\s*chunk[^)]+\)", "", t, flags=re.I)
#     t = re.sub(r"\s*\[chunk\s*\d+[^\]]*\]", "", t, flags=re.I)
#     t = re.sub(r"\s*\[Passage\s*\d+[^\]]*\]", "", t, flags=re.I)
#     t = re.sub(r"[ \t]{2,}", " ", t)
#     return t.strip()


# def strip_trailing_filler_lines(text: str) -> str:
#     """Remove a lone greeting/ack line accidentally emitted after the answer (e.g. 'hi')."""
#     lines = (text or "").splitlines()
#     while len(lines) >= 2:
#         last = lines[-1].strip().lower()
#         if last in _TRAIL_FILLER_LINES:
#             lines.pop()
#         else:
#             break
#     return "\n".join(lines).strip()


# def format_gemma_chat(question, context):
#     return f"""<start_of_turn>system
# You are a helpful assistant. Answer using only the provided context.
# <end_of_turn>
# <start_of_turn>user
# Context:
# {context}

# Question:
# {question}
# <end_of_turn>
# <start_of_turn>model
# """


# def _is_denial_like(text: str) -> bool:
#     t = (text or "").lower()
#     return (
#         "equipment intelligence assistant" in t
#         and "please ask something related" in t
#     )


# def _dedupe_lines(text: str) -> str:
#     lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
#     out: List[str] = []
#     seen = set()
#     for ln in lines:
#         key = re.sub(r"\s+", " ", ln.lower()).strip(" .")
#         if key in seen:
#             continue
#         seen.add(key)
#         out.append(ln)
#     return "\n".join(out).strip()


# def _strip_prompt_echo(text: str) -> str:
#     t = (text or "")

#     # Keep only content after an echoed "Answer:" marker if present.
#     ans_idx = t.lower().rfind("answer:")
#     if ans_idx != -1:
#         t = t[ans_idx + len("answer:"):]

#     # Remove leaked prompt/debug lines.
#     cleaned: List[str] = []
#     for line in t.splitlines():
#         low = line.strip().lower()
#         if low.startswith("user query:"):
#             continue
#         if low.startswith("retrieved context:"):
#             continue
#         if low.startswith("rag input contract"):
#             continue
#         if re.match(r"^\[\d+\]\s+source:\s", low):
#             continue
#         cleaned.append(line)

#     return "\n".join(cleaned).strip()


# def sanitize_generated_answer(answer: str) -> str:
#     text = (answer or "").strip()
#     if not text:
#         return text

#     # Remove leaked prompt/user-context echoes first.
#     text = _strip_prompt_echo(text)
#     if not text:
#         return ""

#     # Hard-stop noisy refusal loops into a single canonical denial line.
#     if _is_denial_like(text):
#         return DENIAL_LINE

#     # Remove common meta/directive artifacts that should not appear in final output.
#     text = re.sub(r"(?im)^as an assistant,?\s*", "", text)
#     text = re.sub(r"(?im)^would you like to know:?\s*$", "", text)
#     text = re.sub(r"\n{3,}", "\n\n", text).strip()

#     # If denial line appears anywhere, normalize to one line.
#     if _is_denial_like(text):
#         return DENIAL_LINE

#     return _dedupe_lines(text)


# def postprocess_rag_answer(text: str) -> str:
#     """
#     Clean model quirks: duplicate bullet sections, trailing follow-up questions, etc.
#     """
#     t = (text or "").strip()
#     if not t:
#         return t

#     # Drop trailing / inline "Would you like to know..." style lines.
#     lines_out: List[str] = []
#     for line in t.splitlines():
#         low = line.strip().lower()
#         if low.startswith("would you like to know"):
#             continue
#         lines_out.append(line)
#     t = "\n".join(lines_out).strip()

#     # Remove duplicate "Bullet point(s):" blocks (keep content before the second header).
#     split_pts = re.split(r"(?im)^\s*bullet\s*points?\s*:\s*$", t)
#     if len(split_pts) >= 3:
#         t = (split_pts[0] + "\n\n" + split_pts[1].strip()).strip()
#     elif len(split_pts) == 2:
#         head, tail = split_pts[0].strip(), split_pts[1].strip()
#         bullet_line = re.compile(rf"(?m)^\s*(?:{re.escape(BULLET_CHAR)}|-)\s+\S")
#         if bullet_line.search(head) and bullet_line.search(tail):
#             t = head

#     # Second "Key points:" duplicate
#     kp = list(re.finditer(r"(?im)^\s*key\s*points\s*:\s*$", t))
#     if len(kp) >= 2:
#         t = t[: kp[1].start()].rstrip()

#     t = normalize_runon_bullet_lines(t)
#     t = normalize_inline_key_points_bullets(t)
#     t = ensure_blank_line_before_key_points(t)
#     t = strip_trailing_filler_lines(t)
#     t = strip_internal_chunk_citations(t)
#     t = re.sub(r"\n{3,}", "\n\n", t).strip()
#     return t


# def normalize_inline_key_points_bullets(text: str) -> str:
#     """
#     Turn 'Key points: • a • b' or 'Key points: - a - b' (single line) into separate bullet lines.
#     Splits on spaced • or spaced hyphen bullets so values like 100-240 VAC stay intact.
#     """
#     t = text or ""
#     if not re.search(r"(?i)key\s*points\s*:", t):
#         return t

#     def fix_segment(segment: str) -> str:
#         m = re.search(r"(?is)(.*?)(\bkey\s*points\s*:\s*)(.+)$", segment)
#         if not m:
#             return segment
#         before, mid, tail = m.group(1), m.group(2), m.group(3).strip()
#         if "\n" in tail:
#             head, _, rest = tail.partition("\n")
#         else:
#             head, rest = tail, ""
#         has_mixed = " - " in head or f" {BULLET_CHAR} " in head
#         starts_bullet = re.match(
#             rf"^\s*(?:{re.escape(BULLET_CHAR)}|-)\s+\S",
#             head,
#         )
#         if not has_mixed and not starts_bullet:
#             return segment
#         raw_items = re.split(rf"\s+(?:{re.escape(BULLET_CHAR)}|-)\s+", head.strip())
#         items: List[str] = []
#         for it in raw_items:
#             it = it.strip()
#             if it.startswith("-"):
#                 it = it.lstrip("-").strip()
#             if it.startswith(BULLET_CHAR):
#                 it = it.lstrip(BULLET_CHAR).strip()
#             if it:
#                 items.append(it)
#         if len(items) < 2:
#             return segment
#         bullets = "\n".join(f"{BULLET_CHAR} {it}" for it in items)
#         rebuilt = f"{before}{mid}\n{bullets}"
#         if rest:
#             rebuilt = f"{rebuilt}\n{rest}"
#         return rebuilt

#     return fix_segment(t)













from typing import Any, Dict, List, Optional
import re

DENIAL_LINE = "I'm an equipment intelligence assistant. Please ask something related to laboratory, analytical, manufacturing, or utility equipment."

BULLET_CHAR = "\u2022"


EQUIPMENT_INTELLIGENCE_SYSTEM_PROMPT = """
You are an equipment intelligence assistant for industrial and lab equipment manuals — operation, calibration, maintenance, qualification, troubleshooting, and Indian regulatory contexts.

Grounding:
- Use RETRIEVED CONTEXT as the source of truth. Do not invent procedures, specs, or details that aren't in context.
- If a chunk is only a heading or image reference, say the manual provides it as a figure/image; don't describe unseen content.

Format: Bullets for lists, headings for sections, prose for facts.


""".strip()


# Active response-format contract. postprocess_rag_answer / ensure_blank_line_before_key_points
# expect: lead paragraph, blank line, **Key points:**, then one "• " bullet per line.
RAG_ANSWER_FORMAT = """
ANSWER FORMAT (for equipment/manual questions with RETRIEVED CONTEXT):
- Start with ONE short paragraph (2-3 sentences) that answers the question directly, using only facts supported by the passages.
- Then a completely blank line, then the header on its own line: **Key points:**
- After the header, put one fact per line, each line starting with "• " (bullet + one space). Exactly one bullet per line — never chain facts with ";" or put two bullets on one line.
- Use 3-8 bullets when the manual lists several rules, specs, or conditions; use fewer only if the context is genuinely narrow. Optional **bold** labels inside a bullet are fine.
- For steps/procedures/how-to: keep every step in the manual's order as its own bullet (or numbered line); do not skip, merge, or summarize away distinct steps, warnings, or tools.
- Do not repeat the user's question. No "Would you like to know...", no closing offers, no chunk/passage IDs.
""".strip()


#
# RAG_ANSWER_FORMAT_AND_GROUNDING = """
# TECHNICAL ANSWERS (when the  asks about equipment/manuals and RETRIEVED CONTEXT is provided below):
# GROUNDING:
# - Prefer exact values, units, limits, names, and step wording from RETRIEVED CONTEXT. Do not invent procedures, specs, or comparisons.
# - If the context is insufficient, say so briefly. If passages conflict, favor the most specific excerpt (table, labeled spec, numbered procedure) and state uncertainty briefly if needed.
# - For definition or "principles" questions, paraphrase the manual closely: use its stated relationships (if/when, before/after, stored vs immediate) rather than a generic textbook story.

# EXTRACTIVE ANSWERS (reduce hallucination):
# - Treat RETRIEVED CONTEXT as the source of truth: when a rule, condition, or procedure appears there, carry it into the answer with the manual's own wording whenever possible.
# - Prefer reusing 1-3 consecutive sentences (or a tight clause) from a passage—optionally in quotation marks—over compressing them into a shorter paraphrase that drops qualifiers or invents new words.
# - Each **Key points** bullet should map to a specific idea in at least one passage; use separate bullets for separate rules (do not merge unrelated constraints into one vague line).

# ANTI-HALLUCINATION (strict):
# - Do not add scientific benefits or motives (e.g. "enhance resolution", "sampling efficiency", "improve sensitivity") unless that benefit language appears in RETRIEVED CONTEXT.
# - Do not assert workflow shapes ("sequentially", "one after another") unless the manual states that ordering explicitly.
# - Do not add quantitative or consistency claims (e.g. "consistent sample volumes", "same volume") unless the manual states them.
# - Do not invent mechanisms (e.g. "minimizing solvent transfer", "preventing complete parking") unless those phrases or equivalent steps appear in RETRIEVED CONTEXT. Prefer the manual's own terms (e.g. flushing, contamination, parking deck).

# COMPLETENESS (principles / methods / "how it works"):
# - Read all passages: if the manual states analysis order (e.g. reverse/backward order), the reason (e.g. contamination), loss conditions (e.g. all loops full), required intermediate steps (e.g. flush gradient before a new deck), or guarantees (e.g. each peak fully analysed before switching), include each as its own bullet when present. Do not omit constraints that appear in context for the sake of a shorter answer.

# CHROMATOGRAPHY / MULTIDIMENSIONAL LC (when relevant):
# - Use valve, loop, cut, deck, parking, modulation, etc. only when those concepts appear in RETRIEVED CONTEXT. Do not add extra instrument detail or comparisons (e.g. vs comprehensive 2D-LC) without support in the text.

# CITATIONS (user-facing):
# - Never include chunk numbers, passage IDs, or bracket tags like [Source: chunk N]. Optional: mention the document file name and/or page from the passage header once if it helps the reader; otherwise omit citations.

# FORMAT:
# - One short paragraph first (2-3 sentences) answering the question directly, using only supported facts from the passages.
# - Put one completely blank line before the line **Key points:** (Markdown: end the paragraph, then a blank line, then the header).
# - After **Key points:**, use one bullet per line: start each line with the character • (bullet), a single space, then exactly one fact; no semicolon chains and no multiple bullets on one line.
# - Use 3-8 bullets when the manual lists several distinct rules or conditions; use fewer only if the context is genuinely narrow.
# - Optional **bold** labels inside a bullet are fine. Prefer faithful excerpts over a single vague summary line.
# - Do not repeat the user question. No "Would you like to know..." or similar closers.
# """.strip()


# METADATA_REFERENCE_INSTRUCTION = """
# PASSAGE HEADERS:
# Each excerpt is labeled with document name and, when available, page and section. Use these only for light traceability (e.g. document name or page). Do not expose internal retrieval labels in the answer.
# """.strip()


# CONVERSATIONAL_TONE_INSTRUCTION = """
# TONE:
# - Be warm and professional for greetings/thanks/acknowledgments.
# - Be structured and precise for technical/equipment queries.
# """.strip()




metadata_reference_instruction = """
**METADATA REFERENCE IN CONTEXT:**
The retrieved context includes metadata with each text chunk. Most chunks come from documents
("Document: <name> | page <N>"), but video transcripts come from minute-long timeframes
("Document: <name> | timestamp <MM:SS-MM:SS>").

When generating your response:
- Use the document title, heading, and page number information to provide accurate source references for documents
- For video transcripts, cite the matching timeframe in the same way you cite a PDF page, using the exact "MM:SS-MM:SS" string from the metadata
- You can naturally reference the source document, section heading, page number, or video timestamp when relevant
- PDF example: "According to Manual.pdf, page 12, in the Safety section..."
- Video example: "As explained in AccessControl.mp4, 01:00-02:00, the visitor entry process requires..."
- Always pair the timestamp with the video filename it came from, matching the pattern "<filename>.mp4, MM:SS-MM:SS" so the UI can turn it into a clickable link
- Do NOT include the literal word "timestamp" in your answer; write only the filename and the time range, separated by a comma (e.g. write "AccessControl.mp4, 01:00-02:00", NOT "AccessControl.mp4, timestamp 01:00-02:00")
- Do NOT invent timestamps that are not present in the retrieved metadata
- Include these references naturally in your response when it adds value to the user's understanding
""".strip()


def _tokenize_words(text: str) -> List[str]:
    out: List[str] = []
    cur = []
    for ch in text.lower():
        if ch.isalnum():
            cur.append(ch)
        elif cur:
            out.append("".join(cur))
            cur = []
    if cur:
        out.append("".join(cur))
    return out




def build_system_prompt() -> str:
    parts = [
        EQUIPMENT_INTELLIGENCE_SYSTEM_PROMPT,
        RAG_ANSWER_FORMAT,
        # RAG_ANSWER_FORMAT_AND_GROUNDING,
        # METADATA_REFERENCE_INSTRUCTION,
        # CONVERSATIONAL_TONE_INSTRUCTION,
        # RAG_REFERENCES_OUTPUT_INSTRUCTION,
        metadata_reference_instruction,
    ]
    return "\n\n".join(parts).strip()


def build_rag_system_prompt() -> str:
    """System / instruction block only (shared across RAG turns; safe to KV-cache as a prefix)."""
    return build_system_prompt()


def _serialize_context(context_chunks: List[Dict[str, Any]]) -> str:
    context_strs: List[str] = []
    for i, ch in enumerate(context_chunks, start=1):
        doc = ch.get("doc_name", "unknown")
        text = ch.get("text", "")
        section = (ch.get("section_path_str") or "").strip()
        page = ch.get("page_number", "")
        timestamp = (ch.get("timestamp") or "").strip()
        is_video = (
            str(ch.get("chunk_type") or "").strip() == "video_segment"
            or str(ch.get("ingest_mode") or "").strip() == "video_transcript"
            or bool(timestamp)
        )
        head_parts = [f"Document: {doc}"]
        if is_video and timestamp:
            head_parts.append(f"timestamp {timestamp}")
        else:
            if page not in ("", "?", None):
                head_parts.append(f"page {page}")
            if section:
                head_parts.append(f"section: {section}")
        header = " | ".join(head_parts)
        context_strs.append(f"Passage [{i}] — {header}\n{text.strip()}")
    return "\n\n".join(context_strs)


def build_rag_retrieved_context_block(context_chunks: List[Dict[str, Any]]) -> str:
    """Serialized retrieved passages only (variable per query)."""
    return _serialize_context(context_chunks)


def build_rag_prompt_static() -> str:
    """
    Fixed prefix: `build_rag_system_prompt()` plus the ``USER QUESTION:`` header.

    Keep this identical across requests so llama-cpp-python ``LlamaRAMCache`` can
    match the longest shared token prefix and reuse KV for the static system block
    when only the dynamic tail (question + ``RETRIEVED CONTEXT``) changes.
    """
    return build_rag_system_prompt() + "\n\nUSER QUESTION:\n"


def build_rag_prompt_dynamic(
    question: str,
    context_chunks: List[Dict[str, Any]],
    *,
    batch_index: Optional[int] = None,
    batch_count: Optional[int] = None,
    previous_answer_draft: Optional[str] = None,
    query_language: Optional[str] = None,
) -> str:
    """Variable tail: user question, retrieved context block, and answer cue."""
    context_block = build_rag_retrieved_context_block(context_chunks)
    lang = (query_language or "").strip()
    lang_block = ""
    if lang:
        if lang.lower() == "hinglish":
            lang_block = (
                "\n\nRESPONSE LANGUAGE:\n"
                "The user's query is Hinglish/Romanized Hindi. The final answer MUST be in Hinglish using Roman script only.\n"
                "- Use Hindi-style sentence structure in English letters, for example: \"JDZ-120 manual ke according...\", "
                "\"motor power rating ... hai\", \"power supply specification ... hai\".\n"
                "- Keep English technical terms, model names, units, dimensions, document titles, page/section references unchanged.\n"
                "- Do NOT answer in pure English.\n"
                "- Do NOT use Devanagari script/Hindi letters.\n"
            )
        elif lang.lower() == "hindi":
            lang_block = (
                "\n\nRESPONSE LANGUAGE:\n"
                "The user's query is Hindi/Devanagari. Write the final answer in Hindi, while keeping English "
                "technical terms, model names, units, dimensions, and document references unchanged when needed.\n"
            )
        elif lang.lower() == "english":
            lang_block = (
                "\n\nRESPONSE LANGUAGE:\n"
                "The user's query is English. Write the final answer in English.\n"
            )
    prev = (previous_answer_draft or "").strip()
    prev_block = ""
    if prev:
        prev_block = (
            "\n\nPREVIOUS ANSWER DRAFT (from earlier batch):\n"
            f"{prev}\n"
            "\n(Use this only for structure continuity."
            "Only append net-new facts supported by this batch. Continue numbering/format if the same section continues.)"
            "**Never repeat or rephrase the previous answer.**\n"
        )
    note = ""
    if (
        batch_index is not None
        and batch_count is not None
        and int(batch_count) > 1
    ):
        bi = int(batch_index)
        bc = int(batch_count)
        if bi <= 1:
            note = (
                f"\n\n(NOTE: Excerpts are split across {bc} parts; "
                f"this is part {bi} of {bc}. "
                "Answer using ONLY the passages below; extract facts from this part; "
                "avoid a long generic introduction.)\n"
            )
        else:
            note = (
                f"\n\n(NOTE: Excerpts are split across {bc} parts; "
                f"this is part {bi} of {bc}. "
                "**Never repeat or rephrase the PREVIOUS ANSWER DRAFT.** "
                "Use ONLY this part as evidence. If this part does not directly add new facts "
                "that answer the user question, output nothing (empty response; zero characters). "
                "Do not write 'not found', 'not provided', 'does not mention', "
                "'document does not provide', or any disclaimer/fallback sentence. "
                "No headings, no preface, no summary, and no reference lines when direct evidence is absent.)\n"
            )
    return f"""{question}{note}{lang_block}{prev_block}

RETRIEVED CONTEXT:
{context_block}

FINAL ANSWER:
"""


def build_rag_prompt_parts(
    question: str,
    context_chunks: List[Dict[str, Any]],
) -> tuple[str, str]:
    return build_rag_prompt_static(), build_rag_prompt_dynamic(question, context_chunks)


def build_rag_prompt(
    question: str,
    context_chunks: List[Dict[str, Any]],
) -> str:
    static, dynamic = build_rag_prompt_parts(question, context_chunks)
    return static + dynamic


def _split_line_multiple_bullets(line: str) -> str:
    s = line.rstrip()
    if s.lower().startswith("http"):
        return line
    b = BULLET_CHAR
    if s.count(b) <= 1:
        return line
    parts = re.split(rf"\s+{re.escape(b)}\s+", s)
    if len(parts) <= 1:
        return line
    first, rest = parts[0].strip(), [p.strip() for p in parts[1:] if p.strip()]
    if not rest:
        return line
    if first.startswith(b):
        body = [first] + [f"{b} {p}" if not p.startswith(b) else p for p in rest]
    else:
        body = [first] + [f"{b} {p}" for p in rest]
    return "\n".join(body)


def normalize_runon_bullet_lines(text: str) -> str:
    """
    Fix common model layout mistakes: chained bullets after ; or sentence end, or multiple • on one line.
    """
    t = (text or "").strip()
    if not t:
        return t
    b = BULLET_CHAR
    be = re.escape(b)
    nb = "\n\n" + b + " "
    t = re.sub(rf";\s*{be}\s*", nb, t)
    t = re.sub(r";\s*-\s+", nb, t)
    t = re.sub(rf"([.?!])\s+{be}\s*", lambda m: m.group(1) + nb, t)
    t = re.sub(r"([.?!])\s+-\s+(?=[A-Z])", lambda m: m.group(1) + nb, t)
    t = re.sub(rf"(:\s*){be}\s*(?=\S)", lambda m: m.group(1) + nb, t)
    lines = [_split_line_multiple_bullets(ln) for ln in t.splitlines()]
    t = "\n".join(lines)
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    return t


def ensure_blank_line_before_key_points(text: str) -> str:
    """Ensure a blank line before 'Key points:' when the model glued it to the paragraph."""
    t = text or ""
    return re.sub(r"(?<!\n)\n(\s*Key points?\s*:)", r"\n\n\1", t, flags=re.I)


_TRAIL_FILLER_LINES = frozenset(
    {
        "hi",
        "hello",
        "hey",
        "ok",
        "okay",
        "thanks",
        "thank you",
        "thankyou",
    }
)


def strip_internal_chunk_citations(text: str) -> str:
    """Remove model echoes of chunk/Source tags (not user-verifiable)."""
    t = text or ""
    t = re.sub(r"\s*\[Source:\s*chunk[^\]]+\]", "", t, flags=re.I)
    t = re.sub(r"\s*\(Source:\s*chunk[^)]+\)", "", t, flags=re.I)
    t = re.sub(r"\s*\[chunk\s*\d+[^\]]*\]", "", t, flags=re.I)
    t = re.sub(r"\s*\[Passage\s*\d+[^\]]*\]", "", t, flags=re.I)
    t = re.sub(r"[ \t]{2,}", " ", t)
    return t.strip()


def strip_trailing_filler_lines(text: str) -> str:
    """Remove a lone greeting/ack line accidentally emitted after the answer (e.g. 'hi')."""
    lines = (text or "").splitlines()
    while len(lines) >= 2:
        last = lines[-1].strip().lower()
        if last in _TRAIL_FILLER_LINES:
            lines.pop()
        else:
            break
    return "\n".join(lines).strip()


def format_gemma_chat(question, context):
    return f"""<start_of_turn>system
You are a helpful assistant. Answer using only the provided context.
<end_of_turn>
<start_of_turn>user
Context:
{context}

Question:
{question}
<end_of_turn>
<start_of_turn>model
"""


def _is_denial_like(text: str) -> bool:
    t = (text or "").lower()
    return (
        "equipment intelligence assistant" in t
        and "please ask something related" in t
    )


def _dedupe_lines(text: str) -> str:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    out: List[str] = []
    seen = set()
    for ln in lines:
        key = re.sub(r"\s+", " ", ln.lower()).strip(" .")
        if key in seen:
            continue
        seen.add(key)
        out.append(ln)
    return "\n".join(out).strip()


def _strip_prompt_echo(text: str) -> str:
    t = (text or "")

    # Keep only content after an echoed "Answer:" marker if present.
    ans_idx = t.lower().rfind("answer:")
    if ans_idx != -1:
        t = t[ans_idx + len("answer:"):]

    # Remove leaked prompt/debug lines.
    cleaned: List[str] = []
    for line in t.splitlines():
        low = line.strip().lower()
        if low.startswith("user query:"):
            continue
        if low.startswith("retrieved context:"):
            continue
        if low.startswith("rag input contract"):
            continue
        if re.match(r"^\[\d+\]\s+source:\s", low):
            continue
        cleaned.append(line)

    return "\n".join(cleaned).strip()


def sanitize_generated_answer(answer: str) -> str:
    text = (answer or "").strip()
    if not text:
        return text

    # Remove leaked prompt/user-context echoes first.
    text = _strip_prompt_echo(text)
    if not text:
        return ""

    # Hard-stop noisy refusal loops into a single canonical denial line.
    if _is_denial_like(text):
        return DENIAL_LINE

    # Remove common meta/directive artifacts that should not appear in final output.
    text = re.sub(r"(?im)^as an assistant,?\s*", "", text)
    text = re.sub(r"(?im)^would you like to know:?\s*$", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    # If denial line appears anywhere, normalize to one line.
    if _is_denial_like(text):
        return DENIAL_LINE

    return _dedupe_lines(text)


def postprocess_rag_answer(text: str) -> str:
    """
    Clean model quirks: duplicate bullet sections, trailing follow-up questions, etc.
    """
    t = (text or "").strip()
    if not t:
        return t

    # Drop trailing / inline "Would you like to know..." style lines.
    lines_out: List[str] = []
    for line in t.splitlines():
        low = line.strip().lower()
        if low.startswith("would you like to know"):
            continue
        lines_out.append(line)
    t = "\n".join(lines_out).strip()

    # Remove duplicate "Bullet point(s):" blocks (keep content before the second header).
    split_pts = re.split(r"(?im)^\s*bullet\s*points?\s*:\s*$", t)
    if len(split_pts) >= 3:
        t = (split_pts[0] + "\n\n" + split_pts[1].strip()).strip()
    elif len(split_pts) == 2:
        head, tail = split_pts[0].strip(), split_pts[1].strip()
        bullet_line = re.compile(rf"(?m)^\s*(?:{re.escape(BULLET_CHAR)}|-)\s+\S")
        if bullet_line.search(head) and bullet_line.search(tail):
            t = head

    # Second "Key points:" duplicate
    kp = list(re.finditer(r"(?im)^\s*key\s*points\s*:\s*$", t))
    if len(kp) >= 2:
        t = t[: kp[1].start()].rstrip()

    t = normalize_runon_bullet_lines(t)
    t = normalize_inline_key_points_bullets(t)
    t = ensure_blank_line_before_key_points(t)
    t = strip_trailing_filler_lines(t)
    t = strip_internal_chunk_citations(t)
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    return t


def normalize_inline_key_points_bullets(text: str) -> str:
    """
    Turn 'Key points: • a • b' or 'Key points: - a - b' (single line) into separate bullet lines.
    Splits on spaced • or spaced hyphen bullets so values like 100-240 VAC stay intact.
    """
    t = text or ""
    if not re.search(r"(?i)key\s*points\s*:", t):
        return t

    def fix_segment(segment: str) -> str:
        m = re.search(r"(?is)(.*?)(\bkey\s*points\s*:\s*)(.+)$", segment)
        if not m:
            return segment
        before, mid, tail = m.group(1), m.group(2), m.group(3).strip()
        if "\n" in tail:
            head, _, rest = tail.partition("\n")
        else:
            head, rest = tail, ""
        has_mixed = " - " in head or f" {BULLET_CHAR} " in head
        starts_bullet = re.match(
            rf"^\s*(?:{re.escape(BULLET_CHAR)}|-)\s+\S",
            head,
        )
        if not has_mixed and not starts_bullet:
            return segment
        raw_items = re.split(rf"\s+(?:{re.escape(BULLET_CHAR)}|-)\s+", head.strip())
        items: List[str] = []
        for it in raw_items:
            it = it.strip()
            if it.startswith("-"):
                it = it.lstrip("-").strip()
            if it.startswith(BULLET_CHAR):
                it = it.lstrip(BULLET_CHAR).strip()
            if it:
                items.append(it)
        if len(items) < 2:
            return segment
        bullets = "\n".join(f"{BULLET_CHAR} {it}" for it in items)
        rebuilt = f"{before}{mid}\n{bullets}"
        if rest:
            rebuilt = f"{rebuilt}\n{rest}"
        return rebuilt

    return fix_segment(t)