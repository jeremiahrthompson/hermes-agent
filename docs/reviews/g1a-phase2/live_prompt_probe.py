#!/usr/bin/env python3
from __future__ import annotations
import json, sys, time
from pathlib import Path
REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
from plugins.memory.chromadb import ChromaDBMemoryProvider
from plugins.memory.chromadb import g1a
from plugins.memory.chromadb.config import ChromaDBConfig
from plugins.memory.chromadb.embedding import get_embedding_function
import chromadb

home = Path('/Users/jeremiah/.hermes')
config = ChromaDBConfig.from_json_file(str(home))
p = ChromaDBMemoryProvider()
p._config = config
p._available = True
p._cron_skipped = False
p._hermes_home = str(home)
p._agent_name = 'rilo'
p._session_id = 'g1a-probe'
p._platform = 'cli'
p._gateway_session_key = None
p._agent_context = 'primary'
p._prompt_source = 'provider_with_legacy_fallback'
p._generated_profile_enabled = True
p._boot_synthesis_enabled = True
p._team_context = ''
p._client = chromadb.HttpClient(host=config.chromadb_host, port=config.chromadb_port)
p._ef = get_embedding_function(
    config.embedding_service_url,
    config.embedding_model,
    fallback_enabled=config.embedding_fallback_enabled,
    fallback_url=config.embedding_fallback_url,
)
p._collections = {'memories': p._client.get_collection(config.collections['memories'])}
user = p._search_for_generated('user')
mem = p._search_for_generated('memory')
scored = g1a.score_results(user + mem)
filtered, d1 = g1a.filter_candidates(scored)
dedup, d2 = g1a.deduplicate_candidates(filtered, embed_fn=p._embed)
selected_user = [c for c in dedup if (c.get('metadata') or {}).get('target') == 'user']
selected_memory = [c for c in dedup if (c.get('metadata') or {}).get('target') != 'user']
selected = selected_user + selected_memory
prompt_selected = g1a.select_synthesis_candidates(selected, limit=8)
prompt = g1a.build_synthesis_prompt(prompt_selected)
result = {
    'available': p._available,
    'user': len(user),
    'memory': len(mem),
    'filtered': len(filtered),
    'dedup': len(dedup),
    'selected': len(selected),
    'prompt_selected': len(prompt_selected),
    'prompt_selected_ids': [c.get('id') for c in prompt_selected],
    'prompt_chars': len(prompt),
    'prompt_preview': prompt[:500],
}
t = time.time()
try:
    out = g1a.synthesize_with_ollama(prompt=prompt)
    result.update({'synth_ok': True, 'latency_ms': int((time.time() - t) * 1000), 'out_chars': len(out), 'out_preview': out[:500]})
except Exception as e:
    result.update({'synth_ok': False, 'latency_ms': int((time.time() - t) * 1000), 'error': type(e).__name__ + ': ' + str(e)})
print(json.dumps(result, indent=2, sort_keys=True))
