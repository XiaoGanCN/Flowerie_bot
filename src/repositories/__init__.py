"""存储仓库层：业务逻辑与具体存储实现解耦。

MemoryManager（业务层）只依赖 MemoryRepository 抽象接口，
不感知 SQL / SQLite 细节；未来可替换为 PostgresRepository / RedisRepository。
"""
