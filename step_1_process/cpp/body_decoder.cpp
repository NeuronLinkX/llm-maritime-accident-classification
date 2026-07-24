#include "../hpp/body_decoder.hpp"

#include <cstdlib>
#include <sys/wait.h>

using namespace body_decoder;

std::string shell_quote(const std::string &value) {
    std::string out = "'";
    for (char c : value) {
        if (c == '\'') {
            out += "'\\''";
        } else {
            out.push_back(c);
        }
    }
    out += "'";
    return out;
}

std::string executable_dir(const std::string &argv0) {
    size_t slash = argv0.find_last_of("/\\");
    if (slash == std::string::npos) return ".";
    if (slash == 0) return "/";
    return argv0.substr(0, slash);
}

int run_pdf_python_decoder(const std::string &argv0,
                           const std::string &input_path,
                           const Options &options) {
    std::string script = executable_dir(argv0) + "/pdf_decoder_py/pdf_to_hwpx_then_decode.py";
    {
        std::ifstream probe(script);
        if (!probe.good()) {
            script = "pdf_decoder_py/pdf_to_hwpx_then_decode.py";
        }
    }
    std::string ocr_mode = "auto";
    if (options.ocr_policy == OcrPolicy::Always) {
        ocr_mode = "always";
    } else if (options.ocr_policy == OcrPolicy::Never) {
        ocr_mode = "never";
    }
    std::string command = "python3 " + shell_quote(script) + " " +
                          shell_quote(input_path) +
                          " --decoder=" + shell_quote(argv0) +
                          " --ocr=" + ocr_mode +
                          " --ocr-lang=" + shell_quote(options.ocr_languages) +
                          " --ocr-dpi=" + std::to_string(options.ocr_dpi) +
                          " --ocr-psm=" + std::to_string(options.ocr_psm) +
                          " --min-native-chars=" +
                          std::to_string(options.min_native_chars);

    int rc = std::system(command.c_str());
    if (rc == -1) return 1;
    if (WIFEXITED(rc)) return WEXITSTATUS(rc);
    return 1;
}

// 프로그램 시작점.
// 인자를 해석하고 입력 포맷을 결정한 뒤, 등록된 디코더를 호출해서
// 최종 JSON 결과를 표준 출력으로 내보낸다.
// 디코딩이 끝나면 후처리 파이프라인을 한 번 더 적용해 문장/키워드 결과를 함께 넣는다.

// CLI entry point: parse arguments, detect/validate the format, dispatch to
// the matching decoder via decoder_registry(), and print the resulting JSON
// on stdout. All decode errors surface as a single "error: ..." line on
// stderr with exit code 1.
int main(int argc, char **argv) {
    if (argc < 2) {
        print_usage(argv[0]);
        return 2;
    }

    try {
        const std::string input_path = argv[1];
        std::string format = "auto";
        int option_start = 2;
        if (argc >= 3 && argv[2][0] != '-') {
            format = lower_ascii(argv[2]);
            option_start = 3;
        }
        Options options = parse_options(argc, argv, option_start);

        if (format == "auto") format = detect_format(input_path);
        if (format == "pdf") {
            return run_pdf_python_decoder(argv[0], input_path, options);
        }

        const auto &registry = decoder_registry();
        auto decoder_it = registry.find(format);
        if (decoder_it == registry.end()) {
            throw std::runtime_error(
                "unknown format '" + format + "' (expected auto|hwp|hwpx|pdf)");
        }

        DecodeResult result = decoder_it->second(input_path, options);
        enrich_decode_result(result);

        print_result(result);
        return 0;
    } catch (const std::exception &e) {
        std::cerr << "error: " << e.what() << '\n';
        return 1;
    }
}
