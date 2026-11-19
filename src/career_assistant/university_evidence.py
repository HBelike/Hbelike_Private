"""按带来源的院校名单核验；未收录不等于不满足。"""
import json
import re
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def _data():
    return json.loads(Path(__file__).with_suffix('.json').read_text(encoding='utf-8'))


def verify_university(jd_quote, cv_text, as_of, education_quote=""):
    """只匹配源文本中的完整学校名称，不接受模型生成的排名数字。"""
    data = _data()
    stages = re.findall(r'本科|硕士|博士|第一学历', jd_quote)
    if stages and (len(stages) != 1 or stages[0] not in education_quote):
        return 'needs_confirmation', '院校条件绑定了特定学历阶段，当前引文不能明确对应。', {}
    categories = [key for key in ('985', '211') if key in jd_quote]
    # 组合条件必须先拆为 ANY/ALL，再分别核验。
    if len(categories) > 1 or (categories and re.search(r'QS', jd_quote, re.I)):
        return 'needs_confirmation', '院校组合条件需要分别核对并明确全部／任一逻辑。', {}
    def found(name):
        pattern = r'(?<![\u4e00-\u9fffA-Za-z])' + re.escape(name) + r'(?=$|[\s，,。；;|/（(、:：])'
        return bool(re.search(pattern, cv_text, re.I))
    if categories:
        category = categories[0]
        names = [name for name in data['schools'][category] if found(name)]
        if len(names) == 1:
            evidence = {'school': names[0], 'category': category, 'source_url': data['sources'][category], 'verified_on': data['verified_on']}
            return 'supported', f'按教育部名单核对，{names[0]}属于{category}工程院校；核验的是院校类别，非候选人学历真实性。', evidence
        return 'needs_confirmation', '未匹配到唯一的名单内学校全称；学校更名、分校、附属院校及学历阶段需进一步确认。', {}
    if re.search(r'QS', jd_quote, re.I):
        years = re.findall(r'20\d{2}', jd_quote)
        edition = int(years[0]) if len(years) == 1 else data['qs']['edition']
        if edition != data['qs']['edition'] or re.search(r'学科|专业|亚洲|就业|商科|可持续', jd_quote):
            return 'needs_confirmation', '指定年份或榜单类型不在当前已核验资料中。', {}
        if not years and as_of.year > data['qs']['edition']:
            return 'needs_confirmation', '默认 QS 榜单资料需更新，请明确采用的榜单年份。', {}
        ranks = re.findall(r'(?:前\s*|top\s*)(\d+)', jd_quote, re.I)
        if len(ranks) != 1:
            return 'needs_confirmation', '请明确 QS 世界大学综合榜的名次门槛。', {}
        matching = [entry for entry in data['qs']['entries'] if found(entry['name'])]
        if len(matching) == 1:
            entry = matching[0]
            evidence = {**entry, 'edition': edition, 'ranking_type': data['qs']['ranking_type'],
                        'source_url': data['sources']['qs'], 'verified_on': data['verified_on'], 'default_edition': not years}
            passed = entry['rank'] <= int(ranks[0])
            return ('supported' if passed else 'unsupported'), f'QS {edition} 世界大学综合榜：{entry["name"]}第{entry["rank"]}名。' + ('年份未指定，采用当前已核验版本。' if not years else ''), evidence
        return 'needs_confirmation', '当前 QS 核验资料覆盖部分院校；未收录或中英文校名无法对应时不推定不满足。', {}
    return 'needs_confirmation', '没有明确可核验的院校类别或排名要求。', {}
