#pragma once

#include "body_decoder_common.hpp"
#include "body_decoder_hwp.hpp"
#include "body_decoder_hwpx.hpp"

namespace body_decoder {

// 모든 포맷 디코더가 공유하는 함수 포인터 시그니처다.
using DecoderFn = DecodeResult (*)(const std::string &, const Options &);

// 경로에서 확장자만 추출해 소문자로 반환한다.
std::string extension_lower(const std::string &path) {
    size_t slash = path.find_last_of("/\\");
    size_t dot = path.find_last_of('.');
    if (dot == std::string::npos ||
        (slash != std::string::npos && dot < slash)) {
        return {};
    }
    return lower_ascii(path.substr(dot + 1));
}

// 매직 바이트와 확장자를 함께 사용해 입력 포맷을 판별한다.
std::string detect_format(const std::string &path) {
    std::ifstream f(path, std::ios::binary);
    if (!f) throw std::runtime_error("cannot open input file: " + path);
    std::array<uint8_t, 8> magic{};
    f.read(reinterpret_cast<char *>(magic.data()), magic.size());
    size_t read = static_cast<size_t>(f.gcount());

    if (read >= 5 && std::memcmp(magic.data(), "%PDF-", 5) == 0) {
        return "pdf";
    }
    static constexpr uint8_t cfb_sig[8] = {
        0xD0, 0xCF, 0x11, 0xE0, 0xA1, 0xB1, 0x1A, 0xE1
    };
    if (read == 8 && std::memcmp(magic.data(), cfb_sig, 8) == 0) {
        return "hwp";
    }
    if (read >= 4 && magic[0] == 'P' && magic[1] == 'K' &&
        ((magic[2] == 3 && magic[3] == 4) ||
         (magic[2] == 5 && magic[3] == 6) ||
         (magic[2] == 7 && magic[3] == 8))) {
        return "hwpx";
    }

    std::string ext = extension_lower(path);
    if (ext == "hwp" || ext == "hwpx" || ext == "pdf") return ext;
    throw std::runtime_error("cannot detect input format; pass hwp|hwpx|pdf");
}

// 범위 제약이 있는 정수형 CLI 옵션을 파싱한다.
int parse_int_option(const std::string &value,
                     const std::string &name,
                     int min_value,
                     int max_value) {
    size_t consumed = 0;
    int parsed = 0;
    try {
        parsed = std::stoi(value, &consumed);
    } catch (...) {
        throw std::runtime_error("invalid integer for " + name + ": " + value);
    }
    if (consumed != value.size() || parsed < min_value || parsed > max_value) {
        throw std::runtime_error("out-of-range value for " + name + ": " + value);
    }
    return parsed;
}

// `--name=value` 형식의 CLI 옵션을 `Options` 구조체로 변환한다.
Options parse_options(int argc, char **argv, int start) {
    Options options;
    for (int i = start; i < argc; ++i) {
        std::string arg = argv[i];
        auto value_after = [&](const std::string &prefix) -> std::optional<std::string> {
            if (starts_with(arg, prefix)) return arg.substr(prefix.size());
            return std::nullopt;
        };

        if (auto v = value_after("--ocr=")) {
            std::string mode = lower_ascii(*v);
            if (mode == "auto") options.ocr_policy = OcrPolicy::Auto;
            else if (mode == "always") options.ocr_policy = OcrPolicy::Always;
            else if (mode == "never") options.ocr_policy = OcrPolicy::Never;
            else throw std::runtime_error("--ocr must be auto|always|never");
        } else if (auto v = value_after("--ocr-lang=")) {
            if (v->empty()) throw std::runtime_error("--ocr-lang cannot be empty");
            options.ocr_languages = *v;
        } else if (auto v = value_after("--ocr-dpi=")) {
            options.ocr_dpi = parse_int_option(*v, "--ocr-dpi", 72, 600);
        } else if (auto v = value_after("--ocr-psm=")) {
            options.ocr_psm = parse_int_option(*v, "--ocr-psm", 0, 13);
        } else if (auto v = value_after("--min-native-chars=")) {
            options.min_native_chars = static_cast<size_t>(
                parse_int_option(*v, "--min-native-chars", 0, 1000000));
        } else {
            throw std::runtime_error("unknown option: " + arg);
        }
    }
    return options;
}

// 사용법과 지원 옵션을 stderr에 출력한다.
void print_usage(const char *argv0) {
    std::cerr
        << "usage: " << argv0
        << " <input_path> [auto|hwp|hwpx|pdf] [options]\n"
        << "options:\n"
        << "  --ocr=auto|always|never\n"
        << "  --ocr-lang=kor+eng\n"
        << "  --ocr-dpi=300\n"
        << "  --ocr-psm=4\n"
        << "  --min-native-chars=80\n";
}

// 문자열 배열을 JSON 배열 리터럴 형태로 직렬화한다.
void print_string_array_json(std::ostringstream &out,
                             const std::vector<std::string> &values) {
    out << '[';
    for (size_t i = 0; i < values.size(); ++i) {
        if (i) out << ',';
        out << '"' << json_escape(values[i]) << '"';
    }
    out << ']';
}

// 디코딩 및 후처리 결과를 최종 JSON 한 줄로 직렬화한다.
void print_result(const DecodeResult &r) {
    std::ostringstream out;
    out << "{\"format\":\"" << json_escape(r.format) << "\",\"paragraphs\":[";
    for (size_t i = 0; i < r.paragraphs.size(); ++i) {
        if (i) out << ',';
        out << "{\"level\":" << r.paragraphs[i].level
            << ",\"tag\":" << r.paragraphs[i].tag
            << ",\"text\":\"" << json_escape(r.paragraphs[i].text) << "\"}";
    }
    out << "],\"record_count\":" << r.record_count
        << ",\"para_text_count\":" << r.paragraphs.size()
        << ",\"decompressed_bytes\":" << r.decompressed_bytes
        << ",\"extraction_mode\":\"" << json_escape(r.extraction_mode) << "\""
        << ",\"ocr_attempted\":" << (r.ocr_attempted ? "true" : "false")
        << ",\"ocr_used\":" << (r.ocr_used ? "true" : "false")
        << ",\"page_count\":" << r.page_count
        << ",\"native_visible_chars\":" << r.native_metrics.visible
        << ",\"chosen_visible_chars\":" << r.chosen_metrics.visible
        << ",\"native_quality_score\":" << r.native_metrics.quality_score
        << ",\"chosen_quality_score\":" << r.chosen_metrics.quality_score
        << ",\"ocr_error\":\"" << json_escape(r.ocr_error) << "\"";

    if (r.processed) {
        out << ",\"normalized_text\":\"" << json_escape(r.processed->normalized_text) << "\"";
        out << ",\"sentences\":";
        print_string_array_json(out, r.processed->sentences);
        out << ",\"keyword_sentences\":{";
        for (size_t i = 0; i < r.processed->keyword_sentences.size(); ++i) {
            if (i) out << ',';
            out << '"' << json_escape(r.processed->keyword_sentences[i].keyword) << "\":";
            print_string_array_json(out, r.processed->keyword_sentences[i].sentences);
        }
        out << '}';
    }

    out << '}';
    std::cout << out.str() << '\n';
}

// 포맷 이름을 실제 디코더 함수에 매핑한 정적 레지스트리를 반환한다.
const std::unordered_map<std::string, DecoderFn> &decoder_registry() {
    static const std::unordered_map<std::string, DecoderFn> table = {
        {"hwp", decode_hwp},
        {"hwpx", decode_hwpx},
    };
    return table;
}

}  // namespace body_decoder
