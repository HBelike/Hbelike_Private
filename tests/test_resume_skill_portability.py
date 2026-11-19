"""简历技能引用资料的种子迁移与主对话挂载。"""
from pathlib import Path
import tempfile

from src.config.config_manager import AppConfig
from src.services.skill_library_service import SkillLibraryService
from src.services.skill_portability import export_skill_trees, seed_skill_tree
from src.career_assistant.skill_runtime import CareerSkillRuntime
from scripts.export_portable_skills import build_manifest


def test_seed_preserves_references_license_and_user_edits_without_scripts():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        skill = root/'source'/'builder'
        (skill/'references').mkdir(parents=True)
        (skill/'scripts').mkdir()
        (skill/'SKILL.md').write_text('---\nname: resume-builder\ndescription: 构建简历\n---\n正文',encoding='utf-8')
        (skill/'references'/'schema.md').write_text('schema snapshot',encoding='utf-8')
        (skill/'LICENSE').write_text('MIT',encoding='utf-8')
        (skill/'scripts'/'run.py').write_text('not deployed',encoding='utf-8')
        exported = export_skill_trees(source_roots=[root/'source'],destination_root=root/'seeds')
        assert exported.copied_count == 3
        resources = build_manifest(root/'seeds')['skills'][0]['resources']
        assert {item['path'] for item in resources} == {'resume-builder/LICENSE', 'resume-builder/references/schema.md'}
        assert all(len(item['sha256']) == 64 for item in resources)
        seeded = seed_skill_tree(seed_root=root/'seeds',destination_root=root/'project'/'.agents'/'skills')
        assert seeded.copied_count == 3
        package = root/'project'/'.agents'/'skills'/'resume-builder'
        assert (package/'references'/'schema.md').read_text() == 'schema snapshot'
        assert (package/'LICENSE').read_text() == 'MIT'
        assert not (package/'scripts').exists()
        (package/'SKILL.md').write_text('user edited',encoding='utf-8')
        assert seed_skill_tree(seed_root=root/'seeds',destination_root=package.parent).copied_count == 0
        assert (package/'SKILL.md').read_text() == 'user edited'


def test_clean_server_without_user_home_can_mount_both_skills_and_inherit():
    project = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as temp:
        root=Path(temp)
        seed_skill_tree(seed_root=project/'deploy'/'skill-seeds',destination_root=root/'.agents'/'skills')
        runtime=CareerSkillRuntime(SkillLibraryService(AppConfig(root,root/'config.yaml',{})))
        for name in ('resume-builder','tailored-resume-generator'):
            assert any(m.name==name and m.source_label=='项目本地' for m in runtime.list_mentions(name))
        result=runtime.resolve([], '/tailored-resume-generator /resume-builder 审查结构和岗位表达')
        assert {skill.name for skill in result.skills} == {'resume-builder','tailored-resume-generator'}
        assert result.user_task_text == '审查结构和岗位表达'
        builder=next(skill for skill in result.skills if skill.name=='resume-builder')
        assert '没有Reactive Resume MCP' in builder.instructions
        assert '尚未按目标版本验证导入' in builder.instructions
        inherited=runtime.resolve_for_conversation([], '继续审查', ['/resume-builder 审查简历'])
        assert inherited.skills[0].name=='resume-builder'
