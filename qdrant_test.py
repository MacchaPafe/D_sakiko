from GPT_SoVITS.rag import services


config = services.RagServiceConfig(
    qdrant_location="./knowledge_base/default_world_info",
    embedding_model_path="./GPT_SoVITS/pretrained_models/multilingual-e5-small"
)
context = services.RetrievalContext(
    current_time=4005
)
service = services.QdrantRagService(
    config=config
)

service.initialize()

while True:
    query = input("请输入查询（输入 'exit' 退出）：")
    if query.lower() == "exit" or not query.strip():
        break
    results = service.query_all(query, context=context, top_k_per_collection=1)
    print("查询结果：")
    for key, value in results.items():
        print("当前查询集合：", key)
        for item in value:
            print(f"score: {item.score:.4f}")
            print(f"{item.document}")
            print()
