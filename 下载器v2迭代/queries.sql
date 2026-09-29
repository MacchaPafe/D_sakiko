-- 查询示例：可在建表后直接执行。
-- Worker 应使用 prepare(...).bind(...) 绑定筛选值，不能拼接用户输入。

-- 1. 某来源角色的 V3 模型；没有 Bestdori ID 不影响此查询。
SELECT id, model_id, COALESCE(name, model_id) AS name, r2_key, sha256
FROM models_v3 WHERE character_id = 'anon' ORDER BY id;

-- 2. 背景与某来源角色的头像；name 为 NULL 时由客户端显示文件名。
SELECT id, r2_key, name FROM images
WHERE kind = 'background' ORDER BY id;
SELECT id, r2_key, name FROM images
WHERE kind = 'avatar' AND character_id = 'anon' ORDER BY id;

-- 乐队与角色无需查询 D1。立绘固定为 images/portraits/{romaji}.png。

-- 维护示例：下列全部为注释，核实真实名称和对象 key 后单独执行。
-- 插入时省略 id，由数据库自增；character_id 填本地字典的 romaji。
-- 背景/头像文件名已核对本地资源；R2 路径为建议映射，上传成功后才执行插入。

-- INSERT INTO models_v3 (model_id, character_id, name, r2_key)
-- VALUES ('adv_live2d_anon_002_live_01', 'anon', '爱音 · 演出服 01',
--         'ournotes/anon/adv_live2d_anon_002_live_01/r1.zip');

-- INSERT INTO images (r2_key, kind)
-- VALUES ('images/backgrounds/adv_bkg_stage_000001.png', 'background');
-- INSERT INTO images (r2_key, kind, character_id)
-- VALUES ('images/chat-icons/anon/adv_data_anon_002_chaticon_hs_1st_01.png', 'avatar', 'anon');

-- 修改名称：
-- UPDATE models_v3 SET name = '新的显示名称'
-- WHERE model_id = 'adv_live2d_anon_002_live_01';

-- 更新包：上传新文件后同时修改 key 与摘要，不能保留旧包的摘要。
-- 此处 NULL 表示新包摘要尚未生成；有真实摘要时替换为 64 位小写十六进制值。
-- UPDATE models_v3
-- SET r2_key = 'ournotes/anon/adv_live2d_anon_002_live_01/r2.zip', sha256 = NULL
-- WHERE model_id = 'adv_live2d_anon_002_live_01';

-- 从目录移除一张图片，不删除 R2 中的文件：
-- DELETE FROM images WHERE r2_key = 'images/backgrounds/example.png';
