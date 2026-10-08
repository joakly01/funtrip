from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AUDIT_AGENT_RUNTIME_",
        env_file=".env",
        env_file_encoding="utf-8",
        populate_by_name=True,
        extra="ignore",
    )

    log_level: str = Field(
        default="INFO",
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_LOG_LEVEL",
            "LOG_LEVEL",
        ),
    )
    database_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_DATABASE_URL",
            "DATABASE_URL",
        ),
    )
    db_host: str = Field(
        default="127.0.0.1",
        validation_alias=AliasChoices("AUDIT_AGENT_RUNTIME_DB_HOST", "DB_HOST"),
    )
    db_port: int = Field(
        default=5432,
        validation_alias=AliasChoices("AUDIT_AGENT_RUNTIME_DB_PORT", "DB_PORT"),
    )
    db_user: str = Field(
        default="postgres",
        validation_alias=AliasChoices("AUDIT_AGENT_RUNTIME_DB_USER", "DB_USER"),
    )
    db_password: str = Field(
        default="postgres",
        validation_alias=AliasChoices("AUDIT_AGENT_RUNTIME_DB_PASSWORD", "DB_PASSWORD"),
    )
    db_name: str = Field(
        default="audit_agent_runtime",
        validation_alias=AliasChoices("AUDIT_AGENT_RUNTIME_DB_NAME", "DB_NAME"),
    )
    ontology_schema_preflight_report: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_ONTOLOGY_SCHEMA_PREFLIGHT_REPORT",
            "ONTOLOGY_SCHEMA_PREFLIGHT_REPORT",
        ),
    )
    ontology_schema_approved_report_hash: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_ONTOLOGY_SCHEMA_APPROVED_REPORT_HASH",
            "ONTOLOGY_SCHEMA_APPROVED_REPORT_HASH",
        ),
    )
    deployment_environment: str = Field(
        default="development",
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_DEPLOYMENT_ENVIRONMENT",
            "DEPLOYMENT_ENVIRONMENT",
        ),
    )
    ontology_auth_session_cookie_name: str = Field(
        default="ontology_session",
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_ONTOLOGY_AUTH_SESSION_COOKIE_NAME",
            "ONTOLOGY_AUTH_SESSION_COOKIE_NAME",
        ),
    )
    ontology_auth_csrf_cookie_name: str = Field(
        default="ontology_csrf",
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_ONTOLOGY_AUTH_CSRF_COOKIE_NAME",
            "ONTOLOGY_AUTH_CSRF_COOKIE_NAME",
        ),
    )
    ontology_auth_cookie_secure: bool | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_ONTOLOGY_AUTH_COOKIE_SECURE",
            "ONTOLOGY_AUTH_COOKIE_SECURE",
        ),
    )
    ontology_auth_session_ttl_seconds: int = Field(
        default=28_800,
        ge=300,
        le=2_592_000,
    )
    ontology_auth_lockout_threshold: int = Field(default=5, ge=2, le=20)
    ontology_auth_lockout_seconds: int = Field(
        default=900,
        ge=60,
        le=86_400,
    )
    ontology_auth_argon2_time_cost: int = Field(default=3, ge=1, le=10)
    ontology_auth_argon2_memory_cost_kib: int = Field(
        default=65_536,
        ge=8_192,
        le=1_048_576,
    )
    ontology_auth_argon2_parallelism: int = Field(default=4, ge=1, le=16)
    ontology_auth_argon2_hash_len: int = Field(default=32, ge=16, le=64)
    ontology_auth_argon2_salt_len: int = Field(default=16, ge=16, le=64)
    ontology_auth_argon2_parameters_version: int = Field(default=1, ge=1)
    # 模型网关总入口；当协议专属 URL 未配置时，OpenAI/Claude 两条链路都会使用它。
    # 生产上接 NewAPI/Kimi 时通常设置为 https://new-api.jointpilot.com/v1。
    model_base_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_MODEL_BASE_URL",
            "MODEL_BASE_URL",
        ),
    )
    # OpenAI-compatible /chat/completions 专属入口，优先级高于 MODEL_BASE_URL。
    openai_model_base_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_OPENAI_MODEL_BASE_URL",
            "OPENAI_MODEL_BASE_URL",
        ),
    )
    # Claude/Anthropic-compatible /messages 专属入口，优先级高于 MODEL_BASE_URL。
    claude_model_base_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_CLAUDE_MODEL_BASE_URL",
            "CLAUDE_MODEL_BASE_URL",
        ),
    )
    # 主模型 API key。保留 DASHSCOPE_API_KEY 只是为了兼容旧环境变量；
    # 新接入统一使用 MODEL_API_KEY，避免某个厂商命名污染 runtime 主链。
    model_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_MODEL_API_KEY",
            "MODEL_API_KEY",
            "DASHSCOPE_API_KEY",
        ),
    )
    # 主对话模型名称。旧 DASHSCOPE_MODEL 仍可读取，但新配置统一写 MODEL_NAME。
    model_name: str = Field(
        default="qwen-max",
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_MODEL_NAME",
            "MODEL_NAME",
            "DASHSCOPE_MODEL",
        ),
    )
    # 会话记忆、压缩、相关回忆等低优先级后台调用使用的模型。
    # 未显式配置时跟随主模型，避免主链和记忆链路意外跑到不同供应商。
    session_context_model_name: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_SESSION_CONTEXT_MODEL_NAME",
            "SESSION_CONTEXT_MODEL_NAME",
            "MEMORY_MODEL_NAME",
            "COMPACT_MODEL_NAME",
        ),
    )
    # Ontology 语义召回使用的 OpenAI-compatible embedding 服务。
    # 当前环境的历史脚本默认指向 172.16.0.106:18082/v1，并使用 m3e 作为模型名。
    embedding_base_url: str = Field(
        default="http://172.16.0.106:18082/v1",
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_EMBEDDING_BASE_URL",
            "EMBEDDING_BASE_URL",
        ),
    )
    # Embedding 服务 API key；本地服务可能不校验，允许为空。
    embedding_api_key: str = Field(
        default="",
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_EMBEDDING_API_KEY",
            "EMBEDDING_API_KEY",
        ),
    )
    # Embedding 模型名作为请求参数透传给服务端；默认保留历史脚本里的 m3e。
    embedding_model: str = Field(
        default="m3e",
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_EMBEDDING_MODEL",
            "EMBEDDING_MODEL",
        ),
    )
    # 当前 embedding 服务返回 1024 维向量；建向量 collection 时需要保持一致。
    embedding_dimension: int = Field(
        default=1024,
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_EMBEDDING_DIMENSION",
            "EMBEDDING_DIMENSION",
        ),
    )
    # 批量生成 ontology embedding 文档时的默认批大小。
    embedding_batch_size: int = Field(
        default=16,
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_EMBEDDING_BATCH_SIZE",
            "EMBEDDING_BATCH_SIZE",
        ),
    )
    # Embedding 服务超时时间；ontology 文档较多时索引构建需要比普通召回更宽松。
    embedding_timeout_seconds: float = Field(
        default=300.0,
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_EMBEDDING_TIMEOUT_SECONDS",
            "EMBEDDING_TIMEOUT_SECONDS",
        ),
    )
    # 单个 ontology search document 送入 embedding 服务前的最大字符数，避免长 JSON/骨架触发网关 413。
    embedding_text_max_chars: int = Field(
        default=400,
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_EMBEDDING_TEXT_MAX_CHARS",
            "EMBEDDING_TEXT_MAX_CHARS",
        ),
    )
    # Ontology 召回阶段从 Milvus 取回的语义候选数量。该值不是最终给模型的实体数，
    # 只是进入后续 hybrid rerank 的候选池大小。
    ontology_semantic_top_k: int = Field(
        default=40,
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_ONTOLOGY_SEMANTIC_TOP_K",
            "ONTOLOGY_SEMANTIC_TOP_K",
        ),
    )
    # Ontology 召回阶段从 PG 搜索文档取回的关键词候选数量。
    ontology_lexical_top_k: int = Field(
        default=80,
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_ONTOLOGY_LEXICAL_TOP_K",
            "ONTOLOGY_LEXICAL_TOP_K",
        ),
    )
    # 关键词候选和向量候选合并后保留给实体/关系扩展的候选数量。
    ontology_rerank_top_k: int = Field(
        default=40,
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_ONTOLOGY_RERANK_TOP_K",
            "ONTOLOGY_RERANK_TOP_K",
        ),
    )
    # Ontology 候选文档 rerank 服务。开启后先使用模型 rerank，失败时回退本地 rerank。
    ontology_rerank_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_ONTOLOGY_RERANK_ENABLED",
            "ONTOLOGY_RERANK_ENABLED",
        ),
    )
    ontology_rerank_url: str = Field(
        default="http://172.16.0.106:11028/rerank",
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_ONTOLOGY_RERANK_URL",
            "ONTOLOGY_RERANK_URL",
        ),
    )
    ontology_rerank_model: str = Field(
        default="bge-reranker-large",
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_ONTOLOGY_RERANK_MODEL",
            "ONTOLOGY_RERANK_MODEL",
        ),
    )
    ontology_rerank_timeout_seconds: float = Field(
        default=30.0,
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_ONTOLOGY_RERANK_TIMEOUT_SECONDS",
            "ONTOLOGY_RERANK_TIMEOUT_SECONDS",
        ),
    )
    ontology_rerank_text_max_chars: int = Field(
        default=300,
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_ONTOLOGY_RERANK_TEXT_MAX_CHARS",
            "ONTOLOGY_RERANK_TEXT_MAX_CHARS",
        ),
    )
    ontology_model_rerank_top_k: int = Field(
        default=20,
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_ONTOLOGY_MODEL_RERANK_TOP_K",
            "ONTOLOGY_MODEL_RERANK_TOP_K",
        ),
    )
    # Ontology 向量检索后端。当前可用值：milvus、pgvector、disabled。
    vector_store: str = Field(
        default="milvus",
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_VECTOR_STORE",
            "VECTOR_STORE",
        ),
    )
    # Milvus 向量库连接信息；用于 ontology embedding document 检索。
    milvus_uri: str = Field(
        default="http://172.16.0.106:29630",
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_MILVUS_URI",
            "MILVUS_URI",
        ),
    )
    milvus_user: str = Field(
        default="root",
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_MILVUS_USER",
            "MILVUS_USER",
        ),
    )
    milvus_password: str = Field(
        default="Milvus",
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_MILVUS_PASSWORD",
            "MILVUS_PASSWORD",
        ),
    )
    milvus_db_name: str = Field(
        default="default",
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_MILVUS_DB_NAME",
            "MILVUS_DB_NAME",
        ),
    )
    milvus_collection: str = Field(
        default="audit_ontology_knowledge",
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_MILVUS_COLLECTION",
            "MILVUS_COLLECTION",
        ),
    )
    # 临时 RAG 知识库使用独立 collection，避免和 ontology 语义召回互相污染。
    rag_milvus_collection: str = Field(
        default="audit_rag_chunks",
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_RAG_MILVUS_COLLECTION",
            "RAG_MILVUS_COLLECTION",
        ),
    )
    rag_search_top_k: int = Field(
        default=30,
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_RAG_SEARCH_TOP_K",
            "RAG_SEARCH_TOP_K",
        ),
    )
    rag_rerank_top_k: int = Field(
        default=8,
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_RAG_RERANK_TOP_K",
            "RAG_RERANK_TOP_K",
        ),
    )
    rag_embedding_text_max_chars: int = Field(
        default=2000,
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_RAG_EMBEDDING_TEXT_MAX_CHARS",
            "RAG_EMBEDDING_TEXT_MAX_CHARS",
        ),
    )
    rag_embedding_batch_size: int = Field(
        default=1,
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_RAG_EMBEDDING_BATCH_SIZE",
            "RAG_EMBEDDING_BATCH_SIZE",
        ),
    )
    rag_law_excel_chunk_max_chars: int = Field(
        default=320,
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_RAG_LAW_EXCEL_CHUNK_MAX_CHARS",
            "RAG_LAW_EXCEL_CHUNK_MAX_CHARS",
        ),
    )
    rag_law_excel_chunk_overlap_chars: int = Field(
        default=40,
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_RAG_LAW_EXCEL_CHUNK_OVERLAP_CHARS",
            "RAG_LAW_EXCEL_CHUNK_OVERLAP_CHARS",
        ),
    )
    rag_law_excel_source_dir: str = Field(
        default="",
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_RAG_LAW_EXCEL_SOURCE_DIR",
            "RAG_LAW_EXCEL_SOURCE_DIR",
        ),
    )
    document_wiki_root: str = Field(
        default="data/document_wiki",
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_DOCUMENT_WIKI_ROOT",
            "DOCUMENT_WIKI_ROOT",
        ),
    )
    document_wiki_api_base_url: str = Field(
        default="",
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_DOCUMENT_WIKI_API_BASE_URL",
            "DOCUMENT_WIKI_API_BASE_URL",
        ),
    )
    document_wiki_api_token: str = Field(
        default="",
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_DOCUMENT_WIKI_API_TOKEN",
            "DOCUMENT_WIKI_API_TOKEN",
        ),
    )
    document_wiki_api_timeout_seconds: float = Field(
        default=60.0,
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_DOCUMENT_WIKI_API_TIMEOUT_SECONDS",
            "DOCUMENT_WIKI_API_TIMEOUT_SECONDS",
        ),
    )
    document_wiki_ocr_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_DOCUMENT_WIKI_OCR_ENABLED",
            "DOCUMENT_WIKI_OCR_ENABLED",
        ),
    )
    document_wiki_ocr_url: str = Field(
        default="",
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_DOCUMENT_WIKI_OCR_URL",
            "DOCUMENT_WIKI_OCR_URL",
        ),
    )
    document_wiki_ocr_timeout_seconds: float = Field(
        default=120.0,
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_DOCUMENT_WIKI_OCR_TIMEOUT_SECONDS",
            "DOCUMENT_WIKI_OCR_TIMEOUT_SECONDS",
        ),
    )
    document_wiki_ocr_min_pdf_text_chars: int = Field(
        default=80,
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_DOCUMENT_WIKI_OCR_MIN_PDF_TEXT_CHARS",
            "DOCUMENT_WIKI_OCR_MIN_PDF_TEXT_CHARS",
        ),
    )
    # Neo4j 图数据库连接信息；用于后续知识图谱持久化与查询。
    neo4j_browser_url: str = Field(
        default="http://172.16.0.106:17474",
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_NEO4J_BROWSER_URL",
            "NEO4J_BROWSER_URL",
        ),
    )
    neo4j_bolt_uri: str = Field(
        default="bolt://172.16.0.106:17687",
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_NEO4J_BOLT_URI",
            "NEO4J_BOLT_URI",
            "NEO4J_URI",
        ),
    )
    neo4j_user: str = Field(
        default="neo4j",
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_NEO4J_USER",
            "NEO4J_USER",
            "NEO4J_USERNAME",
        ),
    )
    neo4j_password: str = Field(
        default="",
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_NEO4J_PASSWORD",
            "NEO4J_PASSWORD",
        ),
    )
    audit_work_neo4j_browser_url: str = Field(
        default="http://172.16.0.106:18474",
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_AUDIT_WORK_NEO4J_BROWSER_URL",
            "AUDIT_WORK_NEO4J_BROWSER_URL",
        ),
    )
    audit_work_neo4j_bolt_uri: str = Field(
        default="bolt://172.16.0.106:18687",
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_AUDIT_WORK_NEO4J_BOLT_URI",
            "AUDIT_WORK_NEO4J_BOLT_URI",
        ),
    )
    audit_work_neo4j_user: str = Field(
        default="neo4j",
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_AUDIT_WORK_NEO4J_USER",
            "AUDIT_WORK_NEO4J_USER",
        ),
    )
    audit_work_neo4j_password: str = Field(
        default="",
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_AUDIT_WORK_NEO4J_PASSWORD",
            "AUDIT_WORK_NEO4J_PASSWORD",
        ),
    )
    # Ontology 图查询后端选择；Neo4j 只作为 PostgreSQL 事实源的可选派生投影。
    ontology_graph_backend: str = Field(
        default="postgres",
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_ONTOLOGY_GRAPH_BACKEND",
            "ONTOLOGY_GRAPH_BACKEND",
        ),
    )
    ontology_graph_max_depth: int = Field(
        default=2,
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_ONTOLOGY_GRAPH_MAX_DEPTH",
            "ONTOLOGY_GRAPH_MAX_DEPTH",
        ),
    )
    ontology_graph_max_items: int = Field(
        default=200,
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_ONTOLOGY_GRAPH_MAX_ITEMS",
            "ONTOLOGY_GRAPH_MAX_ITEMS",
        ),
    )
    ontology_graph_query_timeout_seconds: float = Field(
        default=5.0,
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_ONTOLOGY_GRAPH_QUERY_TIMEOUT_SECONDS",
            "ONTOLOGY_GRAPH_QUERY_TIMEOUT_SECONDS",
        ),
    )
    # wire format 选择：openai 构造 Chat Completions payload；claude 构造 Anthropic Messages payload。
    model_protocol: str = Field(
        default="openai",
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_MODEL_PROTOCOL",
            "MODEL_PROTOCOL",
        ),
    )
    # 是否向供应商请求 reasoning/thinking 能力。关闭时 Kimi 使用官方
    # thinking={"type":"disabled"}，其他 OpenAI-compatible 模型不回放 reasoning_content。
    model_enable_thinking: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_MODEL_ENABLE_THINKING",
            "MODEL_ENABLE_THINKING",
        ),
    )
    # thinking token 预算；只有 MODEL_ENABLE_THINKING=true 时才会写入请求 payload。
    model_thinking_budget: int | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_MODEL_THINKING_BUDGET",
            "MODEL_THINKING_BUDGET",
        ),
    )
    # 单次模型 HTTP 调用超时。Agent 工具链可能较长，默认值刻意高于普通接口超时。
    model_timeout_seconds: float = Field(
        default=300.0,
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_MODEL_TIMEOUT_SECONDS",
            "MODEL_TIMEOUT_SECONDS",
        ),
    )
    # 外层历史 compact 触发阈值；超过该消息数时会先摘要早期历史再进入 queryLoop。
    compaction_max_message_count: int = Field(
        default=48,
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_COMPACTION_MAX_MESSAGE_COUNT",
            "COMPACTION_MAX_MESSAGE_COUNT",
        ),
    )
    # 外层历史 compact 后保留的最近消息数。它决定摘要之外还给模型多少原始尾部上下文。
    compaction_keep_recent: int = Field(
        default=12,
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_COMPACTION_KEEP_RECENT",
            "COMPACTION_KEEP_RECENT",
        ),
    )
    # 控制模型上下文里的旧 tool_result 清理。0 表示不启用；大于 0 时，
    # 每次模型采样前只保留最近 N 个工具结果，其余替换为稳定占位文本。
    # 这只影响模型可见 transcript，不影响 SSE/tool_result 事件里的完整结果。
    tool_result_keep_recent: int = Field(
        default=0,
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_TOOL_RESULT_KEEP_RECENT",
            "TOOL_RESULT_KEEP_RECENT",
        ),
    )
    # 单轮模型请求中连续 tool_result 的聚合字符预算；超过后按 tool_use_id
    # 生成稳定 replacement。调小可保护上下文窗口，调大可让模型看到更多原始结果。
    tool_result_max_model_chars: int = Field(
        default=30_000,
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_TOOL_RESULT_MAX_MODEL_CHARS",
            "TOOL_RESULT_MAX_MODEL_CHARS",
        ),
    )
    # tool_result 被预算裁剪时保留的 JSON 预览字符数；只影响模型可见内容，
    # 不影响数据库和前端事件里的完整工具结果。
    tool_result_preview_chars: int = Field(
        default=2_000,
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_TOOL_RESULT_PREVIEW_CHARS",
            "TOOL_RESULT_PREVIEW_CHARS",
        ),
    )
    session_memory_min_incremental_messages: int = Field(
        default=8,
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_SESSION_MEMORY_MIN_INCREMENTAL_MESSAGES",
            "SESSION_MEMORY_MIN_INCREMENTAL_MESSAGES",
        ),
    )
    # session memory 的强制刷新阈值；即使增量判断不明显，累计到该消息数也会刷新记忆。
    session_memory_force_refresh_messages: int = Field(
        default=14,
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_SESSION_MEMORY_FORCE_REFRESH_MESSAGES",
            "SESSION_MEMORY_FORCE_REFRESH_MESSAGES",
        ),
    )
    # 过滤过短 assistant 输出，避免寒暄或空回答触发无意义的 session memory 更新。
    session_memory_min_assistant_chars: int = Field(
        default=240,
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_SESSION_MEMORY_MIN_ASSISTANT_CHARS",
            "SESSION_MEMORY_MIN_ASSISTANT_CHARS",
        ),
    )
    # 至少出现多少个工具结果后才认为本轮有可沉淀的操作事实。
    session_memory_min_tool_results: int = Field(
        default=2,
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_SESSION_MEMORY_MIN_TOOL_RESULTS",
            "SESSION_MEMORY_MIN_TOOL_RESULTS",
        ),
    )
    # 主链模型可见的 Project Memory 来源。默认不读取仓库 memory/MEMORY.md；
    # memory/ 目录是 Codex 开发记忆，不应进入 /api/router/ask 的模型上下文。
    runtime_project_memory_path: str = Field(
        default="",
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_PROJECT_MEMORY_PATH",
            "PROJECT_MEMORY_PATH",
        ),
    )
    # extension agent 根目录。默认扫描包内 extensions/*/agent.json；
    # 测试或部署可通过环境变量覆盖。
    runtime_agents_dir: str = Field(
        default="",
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_AGENTS_DIR",
            "RUNTIME_AGENTS_DIR",
        ),
    )
    # compaction 边界至少前进多少条消息才更新持久化 compact state，避免频繁重写相近摘要。
    session_compaction_min_boundary_advance: int = Field(
        default=6,
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_SESSION_COMPACTION_MIN_BOUNDARY_ADVANCE",
            "SESSION_COMPACTION_MIN_BOUNDARY_ADVANCE",
        ),
    )
    # 是否启用金融衍生业务审计整改 rectification 工具链（HTTP 调用 rectification 后端）。
    rectification_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_RECTIFICATION_ENABLED",
            "RECTIFICATION_ENABLED",
        ),
    )
    # rectification FastAPI 服务根地址（历史 HTTP 代理配置，原生 PG 工具链不再使用）。
    rectification_base_url: str = Field(
        default="http://127.0.0.1:8081",
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_RECTIFICATION_BASE_URL",
            "RECTIFICATION_BASE_URL",
        ),
    )
    # 整改业务 PostgreSQL schema，默认 RECTIFY。
    rectification_db_schema: str = Field(
        default="RECTIFY",
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_RECTIFICATION_DB_SCHEMA",
            "RECTIFICATION_DB_SCHEMA",
        ),
    )
    # 调用 rectification HTTP API 的超时时间（秒）。
    rectification_timeout_seconds: float = Field(
        default=120.0,
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_RECTIFICATION_TIMEOUT_SECONDS",
            "RECTIFICATION_TIMEOUT_SECONDS",
        ),
    )
    # 全业务整改模板 Pipeline 配置文件路径；为空时使用内置 data/config.json。
    rectification_template_config_path: str = Field(
        default="",
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_RECTIFICATION_TEMPLATE_CONFIG_PATH",
            "RECTIFICATION_TEMPLATE_CONFIG_PATH",
        ),
    )
    # 全业务整改模板 Pipeline 输出根目录；为空时为 /tmp/audit-agent-runtime/rectification-template/<run_id>。
    rectification_template_output_dir: str = Field(
        default="",
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_RECTIFICATION_TEMPLATE_OUTPUT_DIR",
            "RECTIFICATION_TEMPLATE_OUTPUT_DIR",
        ),
    )
    # 整改模板 Excel 上传目录；为空时为 /tmp/audit-agent-runtime/rectification-template/uploads。
    rectification_template_upload_dir: str = Field(
        default="",
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_RECTIFICATION_TEMPLATE_UPLOAD_DIR",
            "RECTIFICATION_TEMPLATE_UPLOAD_DIR",
        ),
    )
    # fast_mode：跳过非必要中间产物与 HTML 报告，仅生成并对外暴露「发现问题整改结果_*.xlsx」。
    rectification_template_fast_mode: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_RECTIFICATION_TEMPLATE_FAST_MODE",
            "RECTIFICATION_TEMPLATE_FAST_MODE",
        ),
    )
    # Step1 LLM 语义归类（rectificationAgent）OpenAI 兼容 API 配置。
    rectify_llm_api_base: str = Field(
        default="",
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_RECTIFY_LLM_API_BASE",
            "RECTIFY_LLM_API_BASE",
        ),
    )
    rectify_llm_api_key: str = Field(
        default="",
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_RECTIFY_LLM_API_KEY",
            "RECTIFY_LLM_API_KEY",
        ),
    )
    rectify_llm_model: str = Field(
        default="",
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_RECTIFY_LLM_MODEL",
            "RECTIFY_LLM_MODEL",
        ),
    )
    rectify_llm_auth_mode: str = Field(
        default="legacy",
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_RECTIFY_LLM_AUTH_MODE",
            "RECTIFY_LLM_AUTH_MODE",
        ),
    )
    rectify_llm_extra_body: str = Field(
        default='{"enable_thinking":false,"max_tokens":2048}',
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_RECTIFY_LLM_EXTRA_BODY",
            "RECTIFY_LLM_EXTRA_BODY",
        ),
    )
    rectify_llm_max_inflight: int = Field(
        default=8,
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_RECTIFY_LLM_MAX_INFLIGHT",
            "RECTIFY_LLM_MAX_INFLIGHT",
        ),
    )
    rectify_llm_retry_max_attempts: int = Field(
        default=1,
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_RECTIFY_LLM_RETRY_MAX_ATTEMPTS",
            "RECTIFY_LLM_RETRY_MAX_ATTEMPTS",
        ),
    )
    rectify_llm_timeout_seconds: int = Field(
        default=3600,
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_RECTIFY_LLM_TIMEOUT_SECONDS",
            "RECTIFY_LLM_TIMEOUT_SECONDS",
        ),
    )
    # 启动时校验必需 tool/skill 的策略：warn 只记录风险，strict 直接阻断，disabled 跳过校验。
    runtime_capability_check: str = Field(
        default="warn",
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_CAPABILITY_CHECK",
            "RUNTIME_CAPABILITY_CHECK",
        ),
    )
    # 逗号分隔的必需 skill id 列表，用于部署健康检查，不会改变模型可见 skill 集合。
    runtime_required_skills: str = Field(
        default="",
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_REQUIRED_SKILLS",
            "RUNTIME_REQUIRED_SKILLS",
        ),
    )
    # 逗号分隔的必需 tool name 列表，用于发现 wiring 漏注册问题。
    runtime_required_tools: str = Field(
        default="",
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_REQUIRED_TOOLS",
            "RUNTIME_REQUIRED_TOOLS",
        ),
    )
    # 页面创建的运行时 skill 写入目录。为空时默认使用包内 skills/user；
    # 加载顺序始终在 builtin 之后，且不会覆盖同名内置 skill。
    runtime_user_skills_dir: str = Field(
        default="",
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_USER_SKILLS_DIR",
            "USER_SKILLS_DIR",
        ),
    )
    # MCP server 配置。支持 JSON 字符串或配置文件路径；第一版用于接入远程
    # Streamable HTTP MCP servers，并把其 tools/list 暴露为 runtime tools。
    mcp_servers: str = Field(
        default="",
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_MCP_SERVERS",
            "MCP_SERVERS",
        ),
    )
    mcp_servers_config_path: str = Field(
        default="",
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_MCP_SERVERS_CONFIG_PATH",
            "MCP_SERVERS_CONFIG_PATH",
        ),
    )
    mcp_tool_timeout_seconds: float = Field(
        default=60.0,
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_MCP_TOOL_TIMEOUT_SECONDS",
            "MCP_TOOL_TIMEOUT_SECONDS",
        ),
    )
    # 报告导出文件的对象存储入口；本地开发可指向 MinIO，生产可指向兼容 S3 的网关。
    minio_endpoint: str = Field(
        default="http://127.0.0.1:9000",
        validation_alias=AliasChoices("AUDIT_AGENT_RUNTIME_MINIO_ENDPOINT", "MINIO_ENDPOINT"),
    )
    # 对象存储 access key，仅用于后端上传/签名下载，不会暴露给模型或前端。
    minio_access_key: str = Field(
        default="",
        validation_alias=AliasChoices("AUDIT_AGENT_RUNTIME_MINIO_ACCESS_KEY", "MINIO_ACCESS_KEY"),
    )
    # 对象存储 secret key；应只放在 .env/环境变量里，不写入仓库配置。
    minio_secret_key: str = Field(
        default="",
        validation_alias=AliasChoices("AUDIT_AGENT_RUNTIME_MINIO_SECRET_KEY", "MINIO_SECRET_KEY"),
    )
    # 报告导出默认 bucket；不存在时由具体存储层决定是否自动创建。
    minio_bucket: str = Field(
        default="audit-reports",
        validation_alias=AliasChoices("AUDIT_AGENT_RUNTIME_MINIO_BUCKET", "MINIO_BUCKET"),
    )
    # MinIO/S3 连接是否使用 HTTPS。只影响后端对象存储客户端。
    minio_secure: bool = Field(
        default=False,
        validation_alias=AliasChoices("AUDIT_AGENT_RUNTIME_MINIO_SECURE", "MINIO_SECURE"),
    )
    # 前端下载报告文件的预签名 URL 有效期，默认 7 天。
    minio_presign_expiry_seconds: int = Field(
        default=7 * 24 * 60 * 60,
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_MINIO_PRESIGN_EXPIRY_SECONDS",
            "MINIO_PRESIGN_EXPIRY_SECONDS",
        ),
    )
    chat_attachment_max_file_bytes: int = Field(
        default=25 * 1024 * 1024,
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_CHAT_ATTACHMENT_MAX_FILE_BYTES",
            "CHAT_ATTACHMENT_MAX_FILE_BYTES",
        ),
    )
    chat_attachment_max_files: int = Field(
        default=5,
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_CHAT_ATTACHMENT_MAX_FILES",
            "CHAT_ATTACHMENT_MAX_FILES",
        ),
    )
    chat_attachment_max_chars_per_file: int = Field(
        default=40_000,
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_CHAT_ATTACHMENT_MAX_CHARS_PER_FILE",
            "CHAT_ATTACHMENT_MAX_CHARS_PER_FILE",
        ),
    )
    chat_attachment_max_total_chars: int = Field(
        default=120_000,
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_CHAT_ATTACHMENT_MAX_TOTAL_CHARS",
            "CHAT_ATTACHMENT_MAX_TOTAL_CHARS",
        ),
    )
    risk_report_db_host: str = Field(
        default="10.200.22.131",
        validation_alias=AliasChoices("AUDIT_AGENT_RUNTIME_RISK_REPORT_DB_HOST", "RISK_REPORT_DB_HOST"),
    )
    risk_report_db_port: int = Field(
        default=5432,
        validation_alias=AliasChoices("AUDIT_AGENT_RUNTIME_RISK_REPORT_DB_PORT", "RISK_REPORT_DB_PORT"),
    )
    risk_report_db_name: str = Field(
        default="postgres",
        validation_alias=AliasChoices("AUDIT_AGENT_RUNTIME_RISK_REPORT_DB_NAME", "RISK_REPORT_DB_NAME"),
    )
    risk_report_db_user: str = Field(
        default="wharehourse_gj",
        validation_alias=AliasChoices("AUDIT_AGENT_RUNTIME_RISK_REPORT_DB_USER", "RISK_REPORT_DB_USER"),
    )
    risk_report_db_password: str = Field(
        default="pass1009@",
        validation_alias=AliasChoices("AUDIT_AGENT_RUNTIME_RISK_REPORT_DB_PASSWORD", "RISK_REPORT_DB_PASSWORD"),
    )
    # Ontology 数据源密码只允许通过该环境密钥进入加密存储；空值表示只读能力可用、秘密写入关闭。
    ontology_datasource_secret_key: str = Field(
        default="",
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_ONTOLOGY_DATASOURCE_SECRET_KEY",
            "ONTOLOGY_DATASOURCE_SECRET_KEY",
        ),
    )
    ontology_rerank_batch_size: int = Field(
        default=32,
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_ONTOLOGY_RERANK_BATCH_SIZE",
            "ONTOLOGY_RERANK_BATCH_SIZE",
        ),
    )
    # 企业 main 的不可变 ResourceRevision 使用独立 collection，禁止与旧领域
    # SearchDocument 混用。每个 Version 再通过 workspace scope hash 隔离。
    enterprise_ontology_milvus_collection: str = Field(
        default="enterprise_ontology_resource_revisions",
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_ENTERPRISE_ONTOLOGY_MILVUS_COLLECTION",
            "ENTERPRISE_ONTOLOGY_MILVUS_COLLECTION",
        ),
    )
    # 在线企业本体解析需要快速降级；批量 embedding/投影继续使用各自较长超时。
    enterprise_ontology_external_timeout_seconds: float = Field(
        default=2.0,
        ge=0.25,
        le=30.0,
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_ENTERPRISE_ONTOLOGY_EXTERNAL_TIMEOUT_SECONDS",
            "ENTERPRISE_ONTOLOGY_EXTERNAL_TIMEOUT_SECONDS",
        ),
    )
    ontology_datasource_connect_timeout: int = Field(
        default=10,
        ge=1,
        le=60,
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_ONTOLOGY_DATASOURCE_CONNECT_TIMEOUT",
            "ONTOLOGY_DATASOURCE_CONNECT_TIMEOUT",
        ),
    )
    ontology_datasource_scan_max_tables: int = Field(
        default=2_000,
        ge=1,
        le=10_000,
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_ONTOLOGY_DATASOURCE_SCAN_MAX_TABLES",
            "ONTOLOGY_DATASOURCE_SCAN_MAX_TABLES",
        ),
    )
    ontology_datasource_scan_max_fields: int = Field(
        default=50_000,
        ge=1,
        le=200_000,
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_ONTOLOGY_DATASOURCE_SCAN_MAX_FIELDS",
            "ONTOLOGY_DATASOURCE_SCAN_MAX_FIELDS",
        ),
    )
    ontology_binding_validation_timeout_ms: int = Field(
        default=5_000,
        ge=100,
        le=30_000,
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_ONTOLOGY_BINDING_VALIDATION_TIMEOUT_MS",
            "ONTOLOGY_BINDING_VALIDATION_TIMEOUT_MS",
        ),
    )
    ontology_binding_validation_max_rows: int = Field(
        default=16,
        ge=1,
        le=100,
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_ONTOLOGY_BINDING_VALIDATION_MAX_ROWS",
            "ONTOLOGY_BINDING_VALIDATION_MAX_ROWS",
        ),
    )
    ontology_binding_validation_max_bytes: int = Field(
        default=32_768,
        ge=1_024,
        le=1_048_576,
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_ONTOLOGY_BINDING_VALIDATION_MAX_BYTES",
            "ONTOLOGY_BINDING_VALIDATION_MAX_BYTES",
        ),
    )
    ontology_instance_query_timeout_ms: int = Field(
        default=5_000,
        ge=100,
        le=30_000,
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_ONTOLOGY_INSTANCE_QUERY_TIMEOUT_MS",
            "ONTOLOGY_INSTANCE_QUERY_TIMEOUT_MS",
        ),
    )
    ontology_instance_query_max_bytes: int = Field(
        default=1_048_576,
        ge=16_384,
        le=16_777_216,
        validation_alias=AliasChoices(
            "AUDIT_AGENT_RUNTIME_ONTOLOGY_INSTANCE_QUERY_MAX_BYTES",
            "ONTOLOGY_INSTANCE_QUERY_MAX_BYTES",
        ),
    )

    @model_validator(mode="after")
    def build_derived_settings(self) -> "Settings":
        if not self.database_url:
            self.database_url = (
                f"postgresql+psycopg://{self.db_user}:{self.db_password}"
                f"@{self.db_host}:{self.db_port}/{self.db_name}"
            )
        normalized_protocol = str(self.model_protocol).strip().lower()
        if normalized_protocol not in {"openai", "claude"}:
            normalized_protocol = "openai"
        self.model_protocol = normalized_protocol
        self.log_level = str(self.log_level).strip().upper() or "INFO"
        self.deployment_environment = (
            str(self.deployment_environment).strip().lower() or "development"
        )
        if self.ontology_auth_cookie_secure is None:
            self.ontology_auth_cookie_secure = self.deployment_environment in {
                "prod",
                "production",
            }
        if self.model_protocol == "claude" and self.claude_model_base_url:
            self.model_base_url = self.claude_model_base_url
        elif self.model_protocol == "openai" and self.openai_model_base_url:
            self.model_base_url = self.openai_model_base_url
        normalized_context_model = (
            str(self.session_context_model_name).strip()
            if self.session_context_model_name
            else ""
        )
        self.session_context_model_name = normalized_context_model or self.model_name
        self.embedding_base_url = str(self.embedding_base_url).strip().rstrip("/")
        self.embedding_api_key = str(self.embedding_api_key or "").strip()
        self.embedding_model = str(self.embedding_model).strip() or "m3e"
        self.embedding_dimension = max(1, int(self.embedding_dimension))
        self.embedding_batch_size = max(1, int(self.embedding_batch_size))
        self.embedding_timeout_seconds = max(1.0, float(self.embedding_timeout_seconds))
        self.embedding_text_max_chars = max(100, int(self.embedding_text_max_chars))
        self.ontology_semantic_top_k = max(1, int(self.ontology_semantic_top_k))
        self.ontology_lexical_top_k = max(1, int(self.ontology_lexical_top_k))
        self.ontology_rerank_top_k = max(1, int(self.ontology_rerank_top_k))
        self.ontology_rerank_url = str(self.ontology_rerank_url).strip()
        self.ontology_rerank_model = str(self.ontology_rerank_model).strip() or "bge-reranker-large"
        self.ontology_rerank_timeout_seconds = max(1.0, float(self.ontology_rerank_timeout_seconds))
        self.ontology_rerank_text_max_chars = max(100, int(self.ontology_rerank_text_max_chars))
        self.ontology_rerank_batch_size = max(
            1,
            min(256, int(self.ontology_rerank_batch_size)),
        )
        self.ontology_model_rerank_top_k = max(1, int(self.ontology_model_rerank_top_k))
        normalized_vector_store = str(self.vector_store).strip().lower()
        if normalized_vector_store not in {"milvus", "pgvector", "disabled"}:
            normalized_vector_store = "milvus"
        self.vector_store = normalized_vector_store
        self.milvus_uri = str(self.milvus_uri).strip().rstrip("/")
        self.milvus_user = str(self.milvus_user or "").strip()
        self.milvus_password = str(self.milvus_password or "").strip()
        self.milvus_db_name = str(self.milvus_db_name).strip() or "default"
        self.milvus_collection = str(self.milvus_collection).strip() or "audit_ontology_knowledge"
        self.enterprise_ontology_milvus_collection = (
            str(self.enterprise_ontology_milvus_collection).strip()
            or "enterprise_ontology_resource_revisions"
        )
        self.enterprise_ontology_external_timeout_seconds = max(
            0.25,
            min(
                30.0,
                float(self.enterprise_ontology_external_timeout_seconds),
            ),
        )
        self.rag_milvus_collection = str(self.rag_milvus_collection).strip() or "audit_rag_chunks"
        self.rag_search_top_k = max(1, int(self.rag_search_top_k))
        self.rag_rerank_top_k = max(1, int(self.rag_rerank_top_k))
        self.rag_embedding_text_max_chars = max(100, int(self.rag_embedding_text_max_chars))
        self.rag_embedding_batch_size = max(1, int(self.rag_embedding_batch_size))
        self.rag_law_excel_chunk_max_chars = max(100, int(self.rag_law_excel_chunk_max_chars))
        self.rag_law_excel_chunk_overlap_chars = max(
            0,
            min(int(self.rag_law_excel_chunk_overlap_chars), self.rag_law_excel_chunk_max_chars // 3),
        )
        self.rag_law_excel_source_dir = str(self.rag_law_excel_source_dir or "").strip()
        self.document_wiki_root = str(self.document_wiki_root or "data/document_wiki").strip()
        self.document_wiki_api_base_url = str(self.document_wiki_api_base_url or "").strip().rstrip("/")
        self.document_wiki_api_token = str(self.document_wiki_api_token or "").strip()
        self.document_wiki_api_timeout_seconds = max(1.0, float(self.document_wiki_api_timeout_seconds))
        self.document_wiki_ocr_url = str(self.document_wiki_ocr_url or "").strip().rstrip("/")
        self.document_wiki_ocr_timeout_seconds = max(1.0, float(self.document_wiki_ocr_timeout_seconds))
        self.document_wiki_ocr_min_pdf_text_chars = max(1, int(self.document_wiki_ocr_min_pdf_text_chars))
        self.neo4j_browser_url = str(self.neo4j_browser_url or "").strip().rstrip("/")
        self.neo4j_bolt_uri = str(self.neo4j_bolt_uri or "").strip().rstrip("/")
        self.neo4j_user = str(self.neo4j_user or "").strip()
        self.neo4j_password = str(self.neo4j_password or "").strip()
        self.audit_work_neo4j_browser_url = str(self.audit_work_neo4j_browser_url or "").strip().rstrip("/")
        self.audit_work_neo4j_bolt_uri = str(self.audit_work_neo4j_bolt_uri or "").strip().rstrip("/")
        self.audit_work_neo4j_user = str(self.audit_work_neo4j_user or self.neo4j_user or "").strip()
        self.audit_work_neo4j_password = str(self.audit_work_neo4j_password or "").strip() or self.neo4j_password
        normalized_graph_backend = str(self.ontology_graph_backend or "postgres").strip().lower()
        if normalized_graph_backend not in {"postgres", "neo4j", "auto"}:
            normalized_graph_backend = "postgres"
        self.ontology_graph_backend = normalized_graph_backend
        self.ontology_graph_max_depth = max(1, min(int(self.ontology_graph_max_depth), 5))
        self.ontology_graph_max_items = max(1, min(int(self.ontology_graph_max_items), 1000))
        self.ontology_graph_query_timeout_seconds = max(1.0, float(self.ontology_graph_query_timeout_seconds))
        if not self.model_base_url and self.model_api_key:
            if self.model_protocol == "claude":
                self.model_base_url = "https://dashscope.aliyuncs.com/apps/anthropic"
            else:
                self.model_base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        # 整改模板 Pipeline LLM：未单独配置 RECTIFY_LLM_* 时，跟随主模型 MODEL_*。
        if not str(self.rectify_llm_api_base or "").strip():
            self.rectify_llm_api_base = str(self.model_base_url or "").strip()
        if not str(self.rectify_llm_api_key or "").strip():
            self.rectify_llm_api_key = str(self.model_api_key or "").strip()
        if not str(self.rectify_llm_model or "").strip():
            self.rectify_llm_model = self.model_name
        self.compaction_max_message_count = max(1, int(self.compaction_max_message_count))
        self.compaction_keep_recent = max(0, int(self.compaction_keep_recent))
        self.tool_result_keep_recent = max(0, int(self.tool_result_keep_recent))
        self.tool_result_max_model_chars = max(1, int(self.tool_result_max_model_chars))
        self.tool_result_preview_chars = max(0, int(self.tool_result_preview_chars))
        self.session_memory_min_incremental_messages = max(
            1, int(self.session_memory_min_incremental_messages)
        )
        self.session_memory_force_refresh_messages = max(
            self.session_memory_min_incremental_messages,
            int(self.session_memory_force_refresh_messages),
        )
        self.session_memory_min_assistant_chars = max(
            1, int(self.session_memory_min_assistant_chars)
        )
        self.session_memory_min_tool_results = max(
            1, int(self.session_memory_min_tool_results)
        )
        self.session_compaction_min_boundary_advance = max(
            1, int(self.session_compaction_min_boundary_advance)
        )
        self.rectification_base_url = str(self.rectification_base_url).strip().rstrip("/")
        self.rectification_db_schema = str(self.rectification_db_schema).strip() or "RECTIFY"
        self.rectification_timeout_seconds = max(1.0, float(self.rectification_timeout_seconds))
        normalized_capability_check = str(self.runtime_capability_check).strip().lower()
        if normalized_capability_check not in {"warn", "strict", "disabled"}:
            normalized_capability_check = "warn"
        self.runtime_capability_check = normalized_capability_check
        self.mcp_tool_timeout_seconds = max(1.0, float(self.mcp_tool_timeout_seconds))
        self.ontology_datasource_secret_key = str(
            self.ontology_datasource_secret_key or ""
        ).strip()
        return self
