#pragma once

#include <zlib.h>

#include <algorithm>
#include <array>
#include <cctype>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstddef>
#include <cstring>
#include <fstream>
#include <functional>
#include <iostream>
#include <limits>
#include <map>
#include <memory>
#include <optional>
#include <regex>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

namespace body_decoder {

// CFBF에서 사용하는 특수 섹터 마커 상수들이다.
constexpr uint32_t CFB_FREESECT = 0xFFFFFFFFu;
constexpr uint32_t CFB_ENDOFCHAIN = 0xFFFFFFFEu;
constexpr uint32_t CFB_FATSECT = 0xFFFFFFFDu;
constexpr uint32_t CFB_DIFSECT = 0xFFFFFFFCu;
constexpr uint32_t CFB_NOSTREAM = 0xFFFFFFFFu;

// 디코딩 결과의 문단 단위 데이터다.
struct Paragraph {
    int level = 0;
    int tag = 0;
    std::string text;
};

// PDF OCR 정책이다.
enum class OcrPolicy { Auto, Always, Never };

// 실행 옵션 묶음이다.
struct Options {
    OcrPolicy ocr_policy = OcrPolicy::Auto;
    std::string ocr_languages = "kor+eng";
    int ocr_dpi = 300;
    // PSM 6("균일한 텍스트 블록")보다 PSM 4("여러 크기의 텍스트가 섞인 단일 열")가
    // 재결서 특유의 표+본문 혼합 레이아웃에서 실측 인식 품질이 더 좋았다.
    int ocr_psm = 4;
    size_t min_native_chars = 80;
};

// 텍스트 품질 평가 결과다.
struct TextMetrics {
    size_t codepoints = 0;
    size_t visible = 0;
    size_t meaningful = 0;
    size_t hangul = 0;
    size_t hangul_syllables = 0;
    size_t replacement = 0;
    size_t control = 0;
    double meaningful_ratio = 0.0;
    double quality_score = 0.0;
};

// 키워드 기준으로 추출한 후속 문장 묶음이다.
struct KeywordSentenceGroup {
    std::string keyword;
    std::vector<std::string> sentences;
};

// 후처리로 생성한 문서 단위 분석 결과다.
struct ProcessedText {
    std::vector<std::string> normalized_paragraphs;
    std::vector<std::string> sentences;
    std::vector<KeywordSentenceGroup> keyword_sentences;
    std::string normalized_text;
};

// 최종 디코딩 결과다.
struct DecodeResult {
    std::string format;
    std::vector<Paragraph> paragraphs;
    size_t record_count = 0;
    size_t decompressed_bytes = 0;

    std::string extraction_mode = "native";
    bool ocr_attempted = false;
    bool ocr_used = false;
    int page_count = 0;
    TextMetrics native_metrics;
    TextMetrics chosen_metrics;
    std::string ocr_error;
    std::shared_ptr<const ProcessedText> processed;
};

// 바이트 포인터에서 16비트 리틀엔디언 정수를 읽는다.
uint16_t rd_u16(const uint8_t *p) {
    return static_cast<uint16_t>(p[0]) |
        (static_cast<uint16_t>(p[1]) << 8);
}

// 바이트 포인터에서 32비트 리틀엔디언 정수를 읽는다.
uint32_t rd_u32(const uint8_t *p) {
    return static_cast<uint32_t>(p[0]) |
        (static_cast<uint32_t>(p[1]) << 8) |
        (static_cast<uint32_t>(p[2]) << 16) |
        (static_cast<uint32_t>(p[3]) << 24);
}

// 바이트 포인터에서 64비트 리틀엔디언 정수를 읽는다.
uint64_t rd_u64(const uint8_t *p) {
    return static_cast<uint64_t>(rd_u32(p)) |
        (static_cast<uint64_t>(rd_u32(p + 4)) << 32);
}

// 파일 전체를 메모리로 읽는다.
std::vector<uint8_t> read_file(const std::string &path) {
    std::ifstream f(path, std::ios::binary);
    if (!f) {
        throw std::runtime_error("cannot open input file: " + path);
    }
    return std::vector<uint8_t>((std::istreambuf_iterator<char>(f)),
                                std::istreambuf_iterator<char>());
}

// ASCII 문자열을 소문자로 정규화한다.
std::string lower_ascii(std::string s) {
    std::transform(s.begin(), s.end(), s.begin(), [](unsigned char c) {
        return static_cast<char>(std::tolower(c));
    });
    return s;
}

// 문자열이 특정 접두사로 시작하는지 검사한다.
bool starts_with(const std::string &s, const std::string &prefix) {
    return s.size() >= prefix.size() &&
        s.compare(0, prefix.size(), prefix) == 0;
}

// 문자열이 특정 접미사로 끝나는지 검사한다.
bool ends_with(const std::string &s, const std::string &suffix) {
    return s.size() >= suffix.size() &&
        s.compare(s.size() - suffix.size(), suffix.size(), suffix) == 0;
}

// 문자열 앞뒤 공백을 제거한 복사본을 반환한다.
std::string trim_copy(const std::string &s) {
    size_t begin = 0;
    while (begin < s.size() && std::isspace(static_cast<unsigned char>(s[begin]))) {
        ++begin;
    }
    size_t end = s.size();
    while (end > begin && std::isspace(static_cast<unsigned char>(s[end - 1]))) {
        --end;
    }
    return s.substr(begin, end - begin);
}

// 코드포인트 하나를 UTF-8 바이트열로 인코딩해 출력 문자열에 추가한다.
void append_utf8(std::string &out, uint32_t cp) {
    if (cp <= 0x7F) {
        out.push_back(static_cast<char>(cp));
    } else if (cp <= 0x7FF) {
        out.push_back(static_cast<char>(0xC0 | (cp >> 6)));
        out.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
    } else if (cp <= 0xFFFF) {
        out.push_back(static_cast<char>(0xE0 | (cp >> 12)));
        out.push_back(static_cast<char>(0x80 | ((cp >> 6) & 0x3F)));
        out.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
    } else if (cp <= 0x10FFFF) {
        out.push_back(static_cast<char>(0xF0 | (cp >> 18)));
        out.push_back(static_cast<char>(0x80 | ((cp >> 12) & 0x3F)));
        out.push_back(static_cast<char>(0x80 | ((cp >> 6) & 0x3F)));
        out.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
    } else {
        append_utf8(out, 0xFFFD);
    }
}

// UTF-16LE 버퍼를 UTF-8 문자열로 변환한다.
// CFBF 이름처럼 NUL 패딩 문자열을 읽을 때는 stop_at_nul을 사용한다.
std::string utf16le_to_utf8(const uint8_t *data, size_t byte_len,
                            bool stop_at_nul = false) {
    std::string out;
    out.reserve(byte_len * 2);
    const size_t units = byte_len / 2;
    size_t i = 0;
    while (i < units) {
        uint16_t w1 = rd_u16(data + i * 2);
        ++i;
        if (stop_at_nul && w1 == 0) break;

        uint32_t cp = w1;
        if (w1 >= 0xD800 && w1 <= 0xDBFF) {
            if (i < units) {
                uint16_t w2 = rd_u16(data + i * 2);
                if (w2 >= 0xDC00 && w2 <= 0xDFFF) {
                    ++i;
                    cp = 0x10000u +
                        ((static_cast<uint32_t>(w1) - 0xD800u) << 10) +
                        (static_cast<uint32_t>(w2) - 0xDC00u);
                } else {
                    cp = 0xFFFD;
                }
            } else {
                cp = 0xFFFD;
            }
        } else if (w1 >= 0xDC00 && w1 <= 0xDFFF) {
            cp = 0xFFFD;
        }
        append_utf8(out, cp);
    }
    return out;
}

// HWP/HWPX에서 공통으로 쓰는 raw DEFLATE 스트림을 압축 해제한다.
std::vector<uint8_t> raw_inflate(const uint8_t *data, size_t len,
                                 size_t size_hint = 0) {
    std::vector<uint8_t> out;
    out.reserve(size_hint ? size_hint : len * 4 + 1024);

    z_stream strm{};
    if (inflateInit2(&strm, -15) != Z_OK) {
        throw std::runtime_error("inflateInit2 failed");
    }

    const size_t max_uInt = std::numeric_limits<uInt>::max();
    size_t input_pos = 0;
    std::array<uint8_t, 1 << 16> chunk{};
    int ret = Z_OK;

    while (ret != Z_STREAM_END) {
        if (strm.avail_in == 0 && input_pos < len) {
            size_t feed = std::min(len - input_pos, max_uInt);
            strm.next_in = const_cast<Bytef *>(data + input_pos);
            strm.avail_in = static_cast<uInt>(feed);
            input_pos += feed;
        }

        strm.next_out = chunk.data();
        strm.avail_out = static_cast<uInt>(chunk.size());
        ret = inflate(&strm, Z_NO_FLUSH);

        if (ret != Z_OK && ret != Z_STREAM_END && ret != Z_BUF_ERROR) {
            inflateEnd(&strm);
            throw std::runtime_error("raw DEFLATE inflate failed with code " +
                                     std::to_string(ret));
        }

        size_t produced = chunk.size() - strm.avail_out;
        out.insert(out.end(), chunk.begin(), chunk.begin() + produced);

        if (ret == Z_BUF_ERROR && strm.avail_in == 0 && input_pos >= len) {
            inflateEnd(&strm);
            throw std::runtime_error("truncated raw DEFLATE stream");
        }
    }

    inflateEnd(&strm);
    return out;
}

// vector 입력을 직접 받는 raw_inflate 편의 함수다.
std::vector<uint8_t> raw_inflate(const std::vector<uint8_t> &input,
                                 size_t size_hint = 0) {
    return raw_inflate(input.data(), input.size(), size_hint);
}

// JSON 문자열 리터럴에 안전하게 넣을 수 있도록 특수문자를 이스케이프한다.
std::string json_escape(const std::string &s) {
    std::ostringstream o;
    for (unsigned char c : s) {
        switch (c) {
            case '"': o << "\\\""; break;
            case '\\': o << "\\\\"; break;
            case '\n': o << "\\n"; break;
            case '\t': o << "\\t"; break;
            case '\r': o << "\\r"; break;
            default:
                if (c < 0x20) {
                    char buf[8];
                    std::snprintf(buf, sizeof(buf), "\\u%04x", c);
                    o << buf;
                } else {
                    o << c;
                }
        }
    }
    return o.str();
}

// 문단 목록을 빈 줄 구분의 평문으로 합쳐 텍스트 품질 평가에 넘긴다.
std::string paragraphs_to_text(const std::vector<Paragraph> &paragraphs) {
    std::string out;
    for (const auto &p : paragraphs) {
        if (!out.empty()) out += "\n\n";
        out += p.text;
    }
    return out;
}

// UTF-8 문자열을 코드포인트 벡터로 변환한다.
// 깨진 시퀀스는 예외 대신 U+FFFD로 치환해 후속 처리가 계속되게 한다.
std::vector<uint32_t> decode_utf8_codepoints(const std::string &s) {
    std::vector<uint32_t> cps;
    cps.reserve(s.size());
    size_t i = 0;
    while (i < s.size()) {
        unsigned char c = static_cast<unsigned char>(s[i]);
        if (c < 0x80) {
            cps.push_back(c);
            ++i;
            continue;
        }

        int need = 0;
        uint32_t cp = 0;
        if ((c & 0xE0) == 0xC0) {
            need = 1;
            cp = c & 0x1F;
        } else if ((c & 0xF0) == 0xE0) {
            need = 2;
            cp = c & 0x0F;
        } else if ((c & 0xF8) == 0xF0) {
            need = 3;
            cp = c & 0x07;
        } else {
            cps.push_back(0xFFFD);
            ++i;
            continue;
        }

        if (i + static_cast<size_t>(need) >= s.size()) {
            cps.push_back(0xFFFD);
            break;
        }

        bool ok = true;
        for (int k = 1; k <= need; ++k) {
            unsigned char cc = static_cast<unsigned char>(s[i + k]);
            if ((cc & 0xC0) != 0x80) {
                ok = false;
                break;
            }
            cp = (cp << 6) | (cc & 0x3F);
        }
        if (!ok) {
            cps.push_back(0xFFFD);
            ++i;
            continue;
        }

        bool overlong = (need == 1 && cp < 0x80) ||
                        (need == 2 && cp < 0x800) ||
                        (need == 3 && cp < 0x10000);
        if (overlong || cp > 0x10FFFF || (cp >= 0xD800 && cp <= 0xDFFF)) {
            cps.push_back(0xFFFD);
        } else {
            cps.push_back(cp);
        }
        i += static_cast<size_t>(need + 1);
    }
    return cps;
}

// 유니코드 공백 계열 문자인지 판정한다.
bool is_unicode_space(uint32_t cp) {
    return cp == 0x20 || cp == 0x09 || cp == 0x0A || cp == 0x0D ||
        cp == 0x00A0 || cp == 0x1680 ||
        (cp >= 0x2000 && cp <= 0x200A) || cp == 0x2028 ||
        cp == 0x2029 || cp == 0x202F || cp == 0x205F || cp == 0x3000;
}

// 품질 평가에서 의미 있는 본문 문자로 볼 수 있는지 판정한다.
bool is_meaningful_codepoint(uint32_t cp) {
    if ((cp >= '0' && cp <= '9') || (cp >= 'A' && cp <= 'Z') ||
        (cp >= 'a' && cp <= 'z')) {
        return true;
    }
    if ((cp >= 0xAC00 && cp <= 0xD7A3) ||
        (cp >= 0x1100 && cp <= 0x11FF) ||
        (cp >= 0x3130 && cp <= 0x318F) ||
        (cp >= 0x4E00 && cp <= 0x9FFF) ||
        (cp >= 0x3040 && cp <= 0x30FF)) {
        return true;
    }
    return false;
}

// 완성형 한글 음절인지 판정한다.
bool is_hangul_syllable(uint32_t cp) {
    return cp >= 0xAC00 && cp <= 0xD7A3;
}

// 텍스트의 가시 문자 수, 의미 문자 비율, 손상 비율을 종합해 품질 점수를 계산한다.
TextMetrics evaluate_text(const std::string &text) {
    TextMetrics m;
    auto cps = decode_utf8_codepoints(text);
    m.codepoints = cps.size();
    for (uint32_t cp : cps) {
        if (cp == 0xFFFD) {
            ++m.replacement;
            continue;
        }
        if (cp < 0x20 && cp != '\n' && cp != '\r' && cp != '\t') {
            ++m.control;
            continue;
        }
        if (is_unicode_space(cp)) continue;
        ++m.visible;
        if (is_meaningful_codepoint(cp)) ++m.meaningful;
        if ((cp >= 0xAC00 && cp <= 0xD7A3) ||
            (cp >= 0x1100 && cp <= 0x11FF) ||
            (cp >= 0x3130 && cp <= 0x318F)) {
            ++m.hangul;
        }
        if (is_hangul_syllable(cp)) ++m.hangul_syllables;
    }

    if (m.visible > 0) {
        m.meaningful_ratio = static_cast<double>(m.meaningful) /
                             static_cast<double>(m.visible);
    }

    const double length_factor =
        std::min(1.0, std::log1p(static_cast<double>(m.visible)) /
                       std::log1p(1200.0));
    const double replacement_penalty =
        m.codepoints == 0 ? 0.0
                          : static_cast<double>(m.replacement) /
                                static_cast<double>(m.codepoints);
    const double control_penalty =
        m.codepoints == 0 ? 0.0
                          : static_cast<double>(m.control) /
                                static_cast<double>(m.codepoints);

    m.quality_score = 0.55 * m.meaningful_ratio + 0.45 * length_factor -
                      1.5 * replacement_penalty - 1.0 * control_penalty;
    m.quality_score = std::clamp(m.quality_score, 0.0, 1.0);
    return m;
}

}  // namespace body_decoder
