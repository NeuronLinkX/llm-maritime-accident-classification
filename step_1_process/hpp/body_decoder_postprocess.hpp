#pragma once

#include "body_decoder_common.hpp"

namespace body_decoder {

// 추출 대상 키워드와 그에 대응하는 표기 변형 목록이다.
struct KeywordRule {
    std::string label;
    std::vector<std::string> aliases;
};

// 키워드 정의를 공유 포인터로 한 번만 생성해 후처리에서 재사용한다.
const std::shared_ptr<const std::vector<KeywordRule>> &keyword_rules() {
    static const auto rules = std::make_shared<const std::vector<KeywordRule>>(
        std::vector<KeywordRule>{
            {"사건 개요", {"사건개요", "사건 개요"}},
            {"일시", {"일시", "사고일시"}},
            {"장소", {"장소", "사고장소"}},
            {"사고 경위", {"사고경위", "사고 경위"}},
        });
    return rules;
}

// 키워드 검색용 정규화 문자열에 포함할 문자 범위를 판정한다.
bool is_searchable_keyword_codepoint(uint32_t cp) {
    return (cp >= '0' && cp <= '9') ||
           (cp >= 'A' && cp <= 'Z') ||
           (cp >= 'a' && cp <= 'z') ||
           (cp >= 0xAC00 && cp <= 0xD7A3);
}

// OCR/PDF 추출물에서 의미 없는 장식 기호를 제거하기 위한 판정 함수다.
bool is_decorative_symbol(uint32_t cp) {
    switch (cp) {
        case 0x25A1:
        case 0x25A0:
        case 0x25CB:
        case 0x25CF:
        case 0x25E6:
        case 0x25AA:
        case 0x2022:
        case 0x203B:
        case 0x2502:
        case 0x2514:
        case 0x2518:
        case 0x2500:
        case 0x250C:
        case 0x2510:
            return true;
        default:
            return cp == '|' || cp == '_' || cp == '`';
    }
}

// 전각 문장부호나 스마트 따옴표를 ASCII 기호로 통일한다.
uint32_t normalize_symbol(uint32_t cp) {
    switch (cp) {
        case 0x2018:
        case 0x2019:
        case 0x2032:
            return '\'';
        case 0x201C:
        case 0x201D:
        case 0x2033:
            return '"';
        case 0x2010:
        case 0x2011:
        case 0x2012:
        case 0x2013:
        case 0x2014:
        case 0x2212:
            return '-';
        case 0xFF1A:
            return ':';
        case 0xFF0C:
            return ',';
        case 0xFF01:
            return '!';
        case 0xFF1F:
            return '?';
        default:
            return cp;
    }
}

// 연속 공백을 하나로 줄이고 앞뒤 공백을 제거한다.
std::string collapse_spaces(const std::string &text) {
    std::string out;
    out.reserve(text.size());
    bool previous_space = false;
    for (unsigned char c : text) {
        if (std::isspace(c)) {
            if (!previous_space && !out.empty()) out.push_back(' ');
            previous_space = true;
        } else {
            out.push_back(static_cast<char>(c));
            previous_space = false;
        }
    }
    return trim_copy(out);
}

// 줄바꿈 제거, 문장부호 정규화, 장식 기호 제거를 수행해 후처리용 평문을 만든다.
std::string normalize_extracted_text(const std::string &text) {
    std::string normalized;
    normalized.reserve(text.size());
    bool pending_space = false;

    for (uint32_t raw_cp : decode_utf8_codepoints(text)) {
        if (raw_cp == 0xFFFD) continue;
        uint32_t cp = normalize_symbol(raw_cp);

        if (cp == '\r' || cp == '\n' || cp == '\t' || is_unicode_space(cp)) {
            pending_space = !normalized.empty();
            continue;
        }
        if (cp < 0x20) continue;
        if (is_decorative_symbol(cp)) {
            // 장식 기호를 그냥 지우면 양옆 단어가 그대로 붙어버려
            // (예: "사건개요□장소" → "사건개요장소") 키워드 경계 판정을 무너뜨린다.
            // 공백이 있었던 것처럼 표시해 단어 경계를 보존한다.
            pending_space = !normalized.empty();
            continue;
        }

        if (pending_space && !normalized.empty() &&
            normalized.back() != ' ' && normalized.back() != '(' &&
            cp != '.' && cp != ',' && cp != ':' && cp != ';' && cp != ')' &&
            cp != '!' && cp != '?') {
            normalized.push_back(' ');
        }
        pending_space = false;

        if ((cp >= 0x20 && cp <= 0x7E) ||
            (cp >= 0xAC00 && cp <= 0xD7A3) ||
            (cp >= 0x1100 && cp <= 0x11FF) ||
            (cp >= 0x3130 && cp <= 0x318F) ||
            (cp >= 0x4E00 && cp <= 0x9FFF)) {
            append_utf8(normalized, cp);
        } else if (cp == '"' || cp == '\'' || cp == '-' || cp == '/' ||
                   cp == '(' || cp == ')' || cp == '[' || cp == ']') {
            append_utf8(normalized, cp);
        }
    }

    normalized = collapse_spaces(normalized);

    std::string spaced;
    spaced.reserve(normalized.size() + normalized.size() / 10);
    for (size_t i = 0; i < normalized.size(); ++i) {
        char c = normalized[i];
        spaced.push_back(c);
        if ((c == '.' || c == '?' || c == '!') && i + 1 < normalized.size()) {
            unsigned char next = static_cast<unsigned char>(normalized[i + 1]);
            if (!std::isspace(next) && next != ')' && next != '"' && next != '\'') {
                spaced.push_back(' ');
            }
        }
    }
    return collapse_spaces(spaced);
}

// 마침표가 날짜/소수점이 아니라 문장 끝인지 보수적으로 판정한다.
bool should_split_after_period(const std::string &text, size_t pos) {
    if (text[pos] != '.') return false;
    auto prev_non_space = [&](size_t start) -> char {
        while (start > 0) {
            --start;
            unsigned char c = static_cast<unsigned char>(text[start]);
            if (!std::isspace(c)) return static_cast<char>(c);
        }
        return '\0';
    };
    auto next_non_space = [&](size_t start) -> char {
        for (size_t i = start; i < text.size(); ++i) {
            unsigned char c = static_cast<unsigned char>(text[i]);
            if (!std::isspace(c)) return static_cast<char>(c);
        }
        return '\0';
    };

    char prev = prev_non_space(pos);
    char next = next_non_space(pos + 1);
    if (std::isdigit(static_cast<unsigned char>(prev)) &&
        std::isdigit(static_cast<unsigned char>(next))) {
        return false;
    }
    return true;
}

// 마침표, 물음표, 느낌표를 기준으로 문장 목록을 만든다.
std::vector<std::string> split_sentences(const std::string &text) {
    std::vector<std::string> sentences;
    std::string current;

    auto flush = [&]() {
        std::string trimmed = collapse_spaces(current);
        if (!trimmed.empty()) sentences.push_back(trimmed);
        current.clear();
    };

    for (size_t i = 0; i < text.size(); ++i) {
        char c = text[i];
        current.push_back(c);

        bool sentence_end = false;
        if (c == '.' && should_split_after_period(text, i)) {
            sentence_end = true;
        } else if (c == '?' || c == '!') {
            sentence_end = true;
        }

        if (!sentence_end) continue;

        size_t j = i + 1;
        while (j < text.size() &&
               (text[j] == '"' || text[j] == '\'' || text[j] == ')' || text[j] == ']')) {
            current.push_back(text[j]);
            ++j;
            i = j - 1;
        }
        flush();
    }
    flush();
    return sentences;
}

// 검색용 코드포인트를 대소문자 무시 기준으로 비교한다.
bool search_codepoint_equal(uint32_t a, uint32_t b) {
    if (a < 0x80 && b < 0x80) {
        return std::tolower(static_cast<unsigned char>(a)) ==
               std::tolower(static_cast<unsigned char>(b));
    }
    return a == b;
}

// 별칭 문자열에서 검색 가능한 코드포인트만 뽑아낸다(공백/기호는 자연히 제외됨).
std::vector<uint32_t> alias_codepoints(const std::string &alias) {
    std::vector<uint32_t> cps;
    for (uint32_t cp : decode_utf8_codepoints(alias)) {
        if (is_searchable_keyword_codepoint(cp)) cps.push_back(cp);
    }
    return cps;
}

// 검색 가능한 코드포인트 하나와 그 원본 바이트 구간이다.
struct SearchUnit {
    uint32_t cp;
    size_t byte_begin;
    size_t byte_end;
};

// 텍스트를 검색 가능한 코드포인트 단위로 분해하면서 원본 바이트 위치를 함께 기록한다.
// 공백/기호는 유닛에서 빠지므로, 유닛끼리 바이트가 바로 이어져 있다는 것은
// 원본에 구분자가 전혀 없었다는 뜻이다(=같은 단어일 가능성이 높다는 신호).
std::vector<SearchUnit> build_search_units(const std::string &text) {
    std::vector<SearchUnit> units;
    size_t i = 0;
    while (i < text.size()) {
        size_t begin = i;
        unsigned char c = static_cast<unsigned char>(text[i]);
        uint32_t cp = 0xFFFD;
        size_t advance = 1;
        if (c < 0x80) {
            cp = c;
        } else if ((c & 0xE0) == 0xC0 && i + 1 < text.size()) {
            cp = ((c & 0x1F) << 6) |
                 (static_cast<unsigned char>(text[i + 1]) & 0x3F);
            advance = 2;
        } else if ((c & 0xF0) == 0xE0 && i + 2 < text.size()) {
            cp = ((c & 0x0F) << 12) |
                 ((static_cast<unsigned char>(text[i + 1]) & 0x3F) << 6) |
                 (static_cast<unsigned char>(text[i + 2]) & 0x3F);
            advance = 3;
        } else if ((c & 0xF8) == 0xF0 && i + 3 < text.size()) {
            cp = ((c & 0x07) << 18) |
                 ((static_cast<unsigned char>(text[i + 1]) & 0x3F) << 12) |
                 ((static_cast<unsigned char>(text[i + 2]) & 0x3F) << 6) |
                 (static_cast<unsigned char>(text[i + 3]) & 0x3F);
            advance = 4;
        }
        i += advance;
        if (!is_searchable_keyword_codepoint(cp)) continue;
        units.push_back({cp, begin, i});
    }
    return units;
}

// start 위치의 유닛이 바로 앞 유닛과 원본에서 붙어 있는지(=단어 중간인지) 검사한다.
bool touches_previous_unit(const std::vector<SearchUnit> &units, size_t start) {
    return start > 0 && units[start - 1].byte_end == units[start].byte_begin;
}

// match_end(마지막으로 매치된 유닛의 다음 인덱스) 바로 뒤 유닛이 원본에서 붙어 있는지 검사한다.
bool touches_following_unit(const std::vector<SearchUnit> &units, size_t match_end) {
    return match_end < units.size() &&
           units[match_end - 1].byte_end == units[match_end].byte_begin;
}

// 유닛 목록에서 별칭이 매치된 구간이다.
struct AliasMatch {
    bool found = false;
    size_t unit_start = 0;
    size_t unit_end = 0;
};

// units 안에서 alias_cps가 가장 먼저 등장하는 위치를 찾는다.
// 앞쪽 경계는 항상 요구하고(다른 단어 중간에서 시작하는 매치를 막기 위함),
// require_end_boundary가 true면 뒤쪽 경계도 요구한다.
AliasMatch find_alias_match(const std::vector<SearchUnit> &units,
                            const std::vector<uint32_t> &alias_cps,
                            bool require_end_boundary) {
    AliasMatch result;
    if (alias_cps.empty() || units.size() < alias_cps.size()) return result;
    for (size_t start = 0; start + alias_cps.size() <= units.size(); ++start) {
        if (touches_previous_unit(units, start)) continue;
        bool matches = true;
        for (size_t k = 0; k < alias_cps.size(); ++k) {
            if (!search_codepoint_equal(units[start + k].cp, alias_cps[k])) {
                matches = false;
                break;
            }
        }
        if (!matches) continue;
        size_t end = start + alias_cps.size();
        if (require_end_boundary && touches_following_unit(units, end)) continue;
        result.found = true;
        result.unit_start = start;
        result.unit_end = end;
        return result;
    }
    return result;
}

// "일시"/"장소"는 OCR이 첫 글자("일"/"장")는 맞게 읽으면서 둘째 글자("시"/"소")를
// 완전히 다른 문자로 잘못 읽는 경우가 잦다(예: "일 A}:", "장 A:"). 이런 문서는
// 표준 별칭 매칭으로는 라벨 자체를 찾지 못해 결과가 통째로 비어버린다.
// 그래서 "일"/"장"으로 시작하는 규칙에 한해, 첫 글자 뒤 짧은 구간 안에
// 콜론이 바로 이어지고 그 사이에 완성형 한글이 없는 패턴을 보조 단서로 쓴다.
// 재결서 서식상 "□ 일 O: …", "□ 장 O: …" 외에는 이 패턴이 나올 일이 거의 없어
// 오탐 위험이 낮다.
uint32_t garbled_label_fallback_codepoint(const KeywordRule &rule) {
    if (rule.label == "일시") return 0xC77C;  // '일'
    if (rule.label == "장소") return 0xC7A5;  // '장'
    return 0;
}

// first_cp로 시작하고 짧은 구간 뒤에 콜론이 오는 위치를 찾아, 콜론 바로 뒤
// 바이트 오프셋을 반환한다. 못 찾으면 nullopt.
std::optional<size_t> find_garbled_short_label(const std::string &text, uint32_t first_cp) {
    if (first_cp == 0) return std::nullopt;
    auto units = build_search_units(text);
    constexpr size_t kWindowBytes = 15;
    for (size_t i = 0; i < units.size(); ++i) {
        if (units[i].cp != first_cp) continue;
        if (touches_previous_unit(units, i)) continue;
        if (i + 1 < units.size() && is_hangul_syllable(units[i + 1].cp)) continue;

        size_t window_end = std::min(text.size(), units[i].byte_end + kWindowBytes);
        std::string window = text.substr(units[i].byte_end, window_end - units[i].byte_end);
        size_t colon = window.find(':');
        if (colon == std::string::npos) continue;

        bool has_hangul_before_colon = false;
        for (uint32_t cp : decode_utf8_codepoints(window.substr(0, colon))) {
            if (is_hangul_syllable(cp)) {
                has_hangul_before_colon = true;
                break;
            }
        }
        if (has_hangul_before_colon) continue;
        return units[i].byte_end + colon + 1;
    }
    return std::nullopt;
}

// 텍스트 안에 지정한 키워드가 (다른 단어에 걸치지 않고) 온전한 형태로 포함되는지 검사한다.
// 예: "선장, 소형선박조종사"에서 공백/쉼표를 지우면 "장소"가 우연히 생기는데,
// 앞뒤 경계 검사 덕분에 이런 거짓 매치는 걸러진다.
bool contains_keyword(const std::string &text, const KeywordRule &rule) {
    auto units = build_search_units(text);
    for (const auto &alias : rule.aliases) {
        if (find_alias_match(units, alias_codepoints(alias), /*require_end_boundary=*/true).found) {
            return true;
        }
    }
    if (find_garbled_short_label(text, garbled_label_fallback_codepoint(rule)).has_value()) {
        return true;
    }
    return false;
}

// 사실관계 서술이 끝나고 판단/이유 서술("이 OOO사건은…", "이 OOO사고는…")이
// 시작되는 지점을 찾는다. 재결서에서 사고경위 다음에 반복적으로 나오는 상투구다.
size_t find_narrative_restart_offset(const std::string &text) {
    static const std::vector<std::string> tails = {"사건은", "사고는"};
    size_t best = std::string::npos;
    for (const auto &tail : tails) {
        size_t search_from = 0;
        while (true) {
            size_t tail_pos = text.find(tail, search_from);
            if (tail_pos == std::string::npos) break;
            search_from = tail_pos + 1;

            size_t window_start = tail_pos > 60 ? tail_pos - 60 : 0;
            size_t lead_pos = text.rfind("이 ", tail_pos);
            if (lead_pos == std::string::npos || lead_pos < window_start) continue;
            // "이"와 "사건은/사고는" 사이에 마침표가 있으면 다른 문장이므로 제외한다.
            if (text.substr(lead_pos, tail_pos - lead_pos).find('.') != std::string::npos) continue;
            if (best == std::string::npos || lead_pos < best) best = lead_pos;
        }
    }
    return best;
}

// 사고경위 다음에 이어지는 다른 절의 표지 라벨이다.
// 이 라벨들이 나타나면 현재 키워드의 문장 수집을 멈춘다.
const std::vector<std::string> &section_boundary_markers() {
    static const std::vector<std::string> markers = {
        "판시요지", "판시 사항", "판시사항",
        "원인판단", "원인 판단",
        "주문", "주 문",
        "이유", "이 유",
        "해양사고관련자",
        "관련법규",
        "참고사항", "참 고 사 항",
        "교훈", "교 훈",
        "재결요지",
        "이 재결요약서는",
        "법적 효력",
    };
    return markers;
}

// 키워드 4종(사건개요/일시/장소/사고경위) 별칭 + 절 표지 라벨(주문/참고사항/
// 이유 등)을 합친 경계 판정용 목록이다.
//
// 둘 다 "위 사고일시와 장소에서"(키워드 라벨이 지시어로 재사용), "위와 같은
// 이유로"(절 표지 "이유"가 그냥 흔한 단어로 쓰임) 처럼 일반 서술문 어휘로도
// 흔히 등장한다. 문장 어디서든 매치되는 즉시 경계로 잘라버리면 사고경위처럼
// 긴 서술형 필드가 엉뚱한 자리에서 통째로 끊긴다. 그래서 이 라벨/표지들은
// 전부 "문장 맨 앞에서 나타날 때"만 새 절의 시작으로 인정한다 — 이 문서
// 포맷에서 실제 절 헤더는 항상 자기 줄(문단)의 맨 앞에 오기 때문에, 이렇게
// 제한해도 진짜 절 전환은 놓치지 않는다.
const std::vector<std::vector<uint32_t>> &boundary_alias_codepoints() {
    static const auto list = [] {
        std::vector<std::vector<uint32_t>> out;
        std::set<std::vector<uint32_t>> seen;
        auto add = [&](const std::string &label) {
            auto cps = alias_codepoints(label);
            if (!cps.empty() && seen.insert(cps).second) out.push_back(cps);
        };
        for (const auto &rule : *keyword_rules()) {
            for (const auto &alias : rule.aliases) add(alias);
        }
        for (const auto &marker : section_boundary_markers()) add(marker);
        return out;
    }();
    return list;
}

// "해양사고관련자"는 문서 앞부분의 관련자 목록 절 표지로도("해양사고관련자"
// 단독, 또는 "해양사고관련자의 행위"처럼 짧은 제목) 쓰이고, 사고경위/
// 사고발생원인 서술문에서는 "해양사고관련자 이 선박 선장 A가 ~~~"처럼
// 문장의 주어로도 흔히 쓰인다. 후자를 절 경계로 잘못 인식하면 수집이 첫
// 문장부터 통째로 끊긴다. 절 제목은 뒤에 짧은 꼬리말(예: "의 행위")만
// 붙는 반면, 문장 주어로 쓰이면 훨씬 긴 서술이 이어지므로, 뒤에 남는
// 유닛 수로 둘을 구분한다.
const std::vector<uint32_t> &related_party_marker_codepoints() {
    static const auto cps = alias_codepoints("해양사고관련자");
    return cps;
}
constexpr size_t kRelatedPartyHeaderMaxTailUnits = 6;

// text가 키워드 라벨/절 표지로 "시작"하는지 확인한다. 문장 맨 앞에서만
// 새 구간의 시작으로 인정한다(위 설명 참고).
size_t find_label_boundary_start_offset(const std::string &text) {
    auto units = build_search_units(text);
    if (units.empty()) return std::string::npos;
    for (const auto &alias_cps : boundary_alias_codepoints()) {
        AliasMatch match = find_alias_match(units, alias_cps, /*require_end_boundary=*/false);
        if (!match.found || match.unit_start != 0) continue;
        if (alias_cps == related_party_marker_codepoints() &&
            units.size() - match.unit_end > kRelatedPartyHeaderMaxTailUnits) {
            continue;  // 뒤에 긴 서술이 이어지면 절 표지가 아니라 문장 주어로 쓰인 것이다.
        }
        return units[0].byte_begin;
    }
    return std::string::npos;
}

// 라벨 경계와 서술 재시작 지점 중 더 이른 것을 다음 구간의 시작점으로 반환한다.
// check_narrative_restart=false면 "이 OOO사건은 ~~~것이다" 판단문 재시작
// 탐지를 끈다. "사고 경위"는 사실관계 서술 뒤에 이런 결론문이 이어지는 게
// 정상이라 이 지점에서 끊어야 하지만, "사고발생원인" 절은 애초에 그 결론문
// 자체로 시작하는 경우가 많아(예: "이 충돌사건은 ~~~ 발생한 것이다.") 이
// 탐지를 그대로 쓰면 첫 문장부터 통째로 끊겨버린다.
size_t find_section_boundary_cut(const std::string &text, bool check_narrative_restart = true) {
    if (text.empty()) return std::string::npos;
    size_t best = find_label_boundary_start_offset(text);
    if (check_narrative_restart) {
        size_t narrative = find_narrative_restart_offset(text);
        if (narrative != std::string::npos && (best == std::string::npos || narrative < best)) {
            best = narrative;
        }
    }
    return best;
}

// 키워드 별칭을 찾아 뒤쪽 텍스트를 잘라낸 결과다.
struct AliasExtraction {
    std::string text;
    bool truncated = false;  // 다음 구간 경계에 걸려 중간에 잘렸는가
    bool found = false;      // 이 문장에서 키워드 자체를 찾았는가
};

// 매치 이후의 원시 텍스트를 다듬고(선행 구두점 제거), 다음 구간 경계에서 잘라낸다.
AliasExtraction finish_alias_extraction(const std::string &raw_suffix, bool check_narrative_restart = true) {
    AliasExtraction result;
    result.found = true;

    std::string suffix = collapse_spaces(trim_copy(raw_suffix));
    while (!suffix.empty() &&
           (suffix.front() == ':' || suffix.front() == '-' || suffix.front() == ',')) {
        suffix.erase(suffix.begin());
        suffix = trim_copy(suffix);
    }

    size_t cut = find_section_boundary_cut(suffix, check_narrative_restart);
    if (cut != std::string::npos) {
        suffix = collapse_spaces(trim_copy(suffix.substr(0, cut)));
        result.truncated = true;
    }
    result.text = suffix;
    return result;
}

// 문장 안에서 키워드 별칭을 찾아 그 뒤쪽 부분만 잘라낸다.
// 잘라낸 결과에 다른 키워드/절 표지가 이어지면 그 앞까지만 남긴다.
AliasExtraction extract_after_alias(const std::string &sentence, const KeywordRule &rule,
                                    bool check_narrative_restart = true) {
    auto units = build_search_units(sentence);
    for (const auto &alias : rule.aliases) {
        AliasMatch match =
            find_alias_match(units, alias_codepoints(alias), /*require_end_boundary=*/true);
        if (!match.found) continue;
        return finish_alias_extraction(sentence.substr(units[match.unit_end - 1].byte_end), check_narrative_restart);
    }

    // 표준 별칭 매칭에 실패하면 "일"/"장" + 근처 콜론 패턴으로 한 번 더 시도한다.
    if (auto pos = find_garbled_short_label(sentence, garbled_label_fallback_codepoint(rule))) {
        return finish_alias_extraction(sentence.substr(*pos), check_narrative_restart);
    }
    return {};
}

// 문단들을 통틀어 키워드가 처음 등장하는 (문단, 문장) 위치를 찾는다.
bool find_keyword_start(const std::vector<std::vector<std::string>> &paragraph_sentences,
                        const KeywordRule &rule, size_t &out_paragraph, size_t &out_sentence) {
    for (size_t p = 0; p < paragraph_sentences.size(); ++p) {
        for (size_t s = 0; s < paragraph_sentences[p].size(); ++s) {
            if (contains_keyword(paragraph_sentences[p][s], rule)) {
                out_paragraph = p;
                out_sentence = s;
                return true;
            }
        }
    }
    return false;
}

// 한 키워드 항목에 모아 담을 최대 문장 수다. 표지 라벨이 전혀 매치되지 않는
// 손상된 입력에서도 결과가 문서 끝까지 무한정 이어지지 않도록 막는 안전장치다.
constexpr size_t kMaxKeywordChunks = 12;

// 한글 음절이 하나도 없거나 너무 짧아 라벨 잔재("L]" 같은 OCR 부스러기,
// 표 셀 한 글자)로 보이는 조각을 걸러낸다. 사건개요/일시/장소/사고경위는
// 항상 한글 문장/값이므로 이 기준으로 잡음을 구분할 수 있다.
// "일시" 값은 "2020. 1. 3. 15:00"처럼 끝에 "경" 같은 한글 표시자 없이
// 숫자와 구두점만으로 적히는 경우가 있다(대개는 "...15:00경"으로 끝나지만
// 원문에 따라 "경"이 아예 없기도 하다). 한글이 하나도 없다고 무조건 버리면
// 이런 값이 사라지므로, 숫자가 충분히 많으면(연/월/일/시/분 다섯 자리 어치)
// 날짜값으로 보고 인정한다.
bool looks_like_date_value(const std::string &text) {
    auto cps = decode_utf8_codepoints(text);
    int digit_count = 0;
    for (uint32_t cp : cps) {
        if (cp >= '0' && cp <= '9') ++digit_count;
    }
    return digit_count >= 6;
}

bool is_meaningful_chunk(const std::string &text, const std::string &label = "") {
    auto cps = decode_utf8_codepoints(text);
    if (cps.size() < 2) return false;
    for (uint32_t cp : cps) {
        if (is_hangul_syllable(cp)) return true;
    }
    if (label == "일시") return looks_like_date_value(text);
    return false;
}

// 키워드가 처음 등장한 지점부터 시작해, 다른 키워드나 절 표지를 만나기 전까지
// 이어지는 문장들을 계속 모은다. 사고경위처럼 문단 여러 개에 걸쳐 서술되는
// 항목도 이렇게 하면 문장을 누락하지 않고 모을 수 있다.
std::vector<std::string> collect_keyword_sentences(
    const std::vector<std::vector<std::string>> &paragraph_sentences,
    const KeywordRule &rule, bool check_narrative_restart = true) {
    std::vector<std::string> collected;

    size_t start_paragraph = 0;
    size_t start_sentence = 0;
    if (!find_keyword_start(paragraph_sentences, rule, start_paragraph, start_sentence)) {
        return collected;
    }

    AliasExtraction inline_result =
        extract_after_alias(paragraph_sentences[start_paragraph][start_sentence], rule, check_narrative_restart);
    if (is_meaningful_chunk(inline_result.text, rule.label)) collected.push_back(inline_result.text);
    bool stop = inline_result.truncated;

    size_t p = start_paragraph;
    size_t s = start_sentence + 1;
    while (!stop && collected.size() < kMaxKeywordChunks) {
        if (p >= paragraph_sentences.size()) break;
        if (s >= paragraph_sentences[p].size()) {
            ++p;
            s = 0;
            continue;
        }
        const std::string &candidate = paragraph_sentences[p][s];
        ++s;
        if (candidate.empty()) continue;

        size_t cut = find_section_boundary_cut(candidate, check_narrative_restart);
        if (cut == 0) {
            stop = true;
        } else if (cut == std::string::npos) {
            if (is_meaningful_chunk(candidate, rule.label)) collected.push_back(candidate);
        } else {
            std::string truncated = collapse_spaces(trim_copy(candidate.substr(0, cut)));
            if (is_meaningful_chunk(truncated, rule.label)) collected.push_back(truncated);
            stop = true;
        }
    }
    return collected;
}

// "재결요약서" 표준 서식이 아니라 전체 "재결서" 원문이 섞여 들어오면
// "사고경위" 라벨 자체가 없다 — 대신 "나. 사고발생원인"/"사고발생 원인"
// 절에 사실상 같은 서술(사고가 어떻게 일어났는지)이 담겨 있으므로, 1차로
// "사고경위"를 찾아보고 못 찾을 때만 이 라벨로 재시도한다.
const KeywordRule &accident_cause_fallback_rule() {
    static const KeywordRule rule{"사고 경위", {"사고발생원인", "사고발생 원인"}};
    return rule;
}

// 키워드별로 후속 문장들을 수집한다.
std::vector<KeywordSentenceGroup> extract_keyword_sentences(
    const std::vector<std::vector<std::string>> &paragraph_sentences) {
    std::vector<KeywordSentenceGroup> groups;
    groups.reserve(keyword_rules()->size());

    for (const auto &rule : *keyword_rules()) {
        KeywordSentenceGroup group;
        group.keyword = rule.label;

        auto sentences = collect_keyword_sentences(paragraph_sentences, rule);
        if (sentences.empty() && rule.label == "사고 경위") {
            sentences = collect_keyword_sentences(paragraph_sentences, accident_cause_fallback_rule(),
                                                  /*check_narrative_restart=*/false);
        }

        std::unordered_set<std::string> seen;
        for (auto &sentence : sentences) {
            if (seen.insert(sentence).second) group.sentences.push_back(std::move(sentence));
        }
        groups.push_back(std::move(group));
    }

    return groups;
}

// 문단 목록을 정규화 텍스트, 문장 목록, 키워드 추출 결과로 확장한다.
std::shared_ptr<const ProcessedText> build_processed_text(
    const std::vector<Paragraph> &paragraphs) {
    auto processed = std::make_shared<ProcessedText>();
    std::vector<std::vector<std::string>> paragraph_sentences;
    paragraph_sentences.reserve(paragraphs.size());

    for (const auto &paragraph : paragraphs) {
        std::string normalized = normalize_extracted_text(paragraph.text);
        if (normalized.empty()) continue;
        processed->normalized_paragraphs.push_back(normalized);

        std::vector<std::string> sentences = split_sentences(normalized);
        if (sentences.empty()) sentences.push_back(normalized);
        for (const auto &sentence : sentences) {
            processed->sentences.push_back(sentence);
        }
        paragraph_sentences.push_back(std::move(sentences));
    }

    for (size_t i = 0; i < processed->normalized_paragraphs.size(); ++i) {
        if (!processed->normalized_text.empty()) processed->normalized_text += ' ';
        processed->normalized_text += processed->normalized_paragraphs[i];
    }
    processed->normalized_text = collapse_spaces(processed->normalized_text);
    processed->keyword_sentences = extract_keyword_sentences(paragraph_sentences);
    return processed;
}

// 디코더 원본 결과에 후처리 분석 결과를 덧붙인다.
void enrich_decode_result(DecodeResult &result) {
    result.processed = build_processed_text(result.paragraphs);
}

}  // namespace body_decoder
