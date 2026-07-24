// STEP 2 — SBERT 모델별 유사도 벤치마크
//
// embed.py가 만들어 둔 embeddings/*.bin(float32, N x D, 행 우선, 이미
// L2 정규화됨) + embeddings/meta.csv(index,id,category,filename)를 읽어서,
// 모델별로 "같은 카테고리 내부 코사인 유사도"와 "다른 카테고리 간 코사인
// 유사도"를 집계하고 비교한다.
//
// 신경망 추론(임베딩 생성)은 Python 몫이고, 이후의 수치 계산(코사인 유사도,
// 통계 집계)은 결정적 산술이라 C++ 몫이다 — STEP1에서 "구조적/결정적 처리는
// C++, 신경망 추론은 Python"으로 나눈 것과 같은 원칙이다.
//
// 벡터가 이미 정규화되어 있으므로 코사인 유사도 = 내적(dot product).
//
// 모델 목록은 하드코딩하지 않고 embeddings/ 안의 *.bin 파일을 스캔해서
// 정한다 — JSON을 파싱할 필요가 없도록 한 설계 그대로다. 각 .bin의 차원은
// 파일 크기 / (레코드 수 * 4바이트)로 스스로 검증한다.

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <queue>
#include <sstream>
#include <string>
#include <unordered_map>
#include <vector>

namespace fs = std::filesystem;

constexpr size_t kPairReportSize = 50;  // 상위/하위 유사 문서 쌍으로 남길 개수

struct Record {
    std::string id;
    std::string category;
    std::string filename;
};

// 문서 쌍 하나의 실제 유사도 값 — 이게 논문 3.2절이 말하는
// "두 재결서 사이의 의미적 유사도를 정량적으로 출력"한 결과 그 자체다.
// 집계(Stats)만 남기면 이 원 자료가 사라지므로, 상위/하위 kPairReportSize개를
// 별도로 보존해 CSV로 남긴다.
struct PairSim {
    double sim;
    int i, j;
    bool operator<(const PairSim& o) const { return sim < o.sim; }
    bool operator>(const PairSim& o) const { return sim > o.sim; }
};

struct Stats {
    double sum = 0.0;
    double sumsq = 0.0;
    long long count = 0;

    void add(double v) {
        sum += v;
        sumsq += v * v;
        count += 1;
    }
    Stats operator-(const Stats& other) const {
        return Stats{sum - other.sum, sumsq - other.sumsq, count - other.count};
    }
    double mean() const { return count > 0 ? sum / static_cast<double>(count) : 0.0; }
    double stddev() const {
        if (count <= 0) return 0.0;
        double m = mean();
        double var = sumsq / static_cast<double>(count) - m * m;
        return var > 0.0 ? std::sqrt(var) : 0.0;
    }
};

struct ModelResult {
    std::string name;
    int dim = 0;
    Stats intra;
    Stats inter;
    double gap() const { return intra.mean() - inter.mean(); }
};

// 쉼표/따옴표를 포함할 수 있는 CSV 한 줄을 파싱한다 (RFC4180 스타일,
// 큰따옴표로 감싼 필드 안의 ""는 리터럴 "로 취급).
static std::vector<std::string> parse_csv_line(const std::string& line) {
    std::vector<std::string> fields;
    std::string cur;
    bool in_quotes = false;
    for (size_t i = 0; i < line.size(); ++i) {
        char c = line[i];
        if (in_quotes) {
            if (c == '"') {
                if (i + 1 < line.size() && line[i + 1] == '"') {
                    cur += '"';
                    ++i;
                } else {
                    in_quotes = false;
                }
            } else {
                cur += c;
            }
        } else {
            if (c == '"') {
                in_quotes = true;
            } else if (c == ',') {
                fields.push_back(cur);
                cur.clear();
            } else {
                cur += c;
            }
        }
    }
    fields.push_back(cur);
    return fields;
}

static std::vector<Record> load_meta(const fs::path& path) {
    std::ifstream f(path);
    if (!f) throw std::runtime_error("meta.csv를 열 수 없습니다: " + path.string());

    std::vector<Record> records;
    std::string line;
    std::getline(f, line);  // 헤더 스킵: index,id,category,filename
    while (std::getline(f, line)) {
        if (line.empty()) continue;
        auto fields = parse_csv_line(line);
        if (fields.size() < 4) continue;
        records.push_back(Record{fields[1], fields[2], fields[3]});
    }
    return records;
}

static std::vector<float> load_embeddings(const fs::path& path, size_t n, int& dim_out) {
    std::ifstream f(path, std::ios::binary | std::ios::ate);
    if (!f) throw std::runtime_error("임베딩 파일을 열 수 없습니다: " + path.string());

    std::streamsize size = f.tellg();
    if (size <= 0 || static_cast<size_t>(size) % (n * sizeof(float)) != 0) {
        throw std::runtime_error(path.string() + ": 파일 크기가 레코드 수(" +
                                  std::to_string(n) + ")와 맞지 않습니다 (size=" +
                                  std::to_string(size) + ")");
    }
    size_t dim = static_cast<size_t>(size) / (n * sizeof(float));
    dim_out = static_cast<int>(dim);

    f.seekg(0);
    std::vector<float> buf(n * dim);
    f.read(reinterpret_cast<char*>(buf.data()), size);
    if (!f) throw std::runtime_error(path.string() + ": 읽기 실패");
    return buf;
}

static inline double dot(const float* a, const float* b, int dim) {
    double s = 0.0;
    for (int k = 0; k < dim; ++k) s += static_cast<double>(a[k]) * static_cast<double>(b[k]);
    return s;
}

int main(int argc, char** argv) {
    fs::path emb_dir = (argc > 1) ? fs::path(argv[1]) : fs::path("../embeddings");

    try {
        auto records = load_meta(emb_dir / "meta.csv");
        size_t n = records.size();
        if (n < 2) {
            std::cerr << "레코드 수가 너무 적습니다 (" << n << "건)\n";
            return 1;
        }
        std::cout << "메타데이터 " << n << "건 로드 (" << emb_dir / "meta.csv" << ")\n";

        // 카테고리별 인덱스 그룹핑
        std::unordered_map<std::string, std::vector<int>> by_category;
        for (size_t i = 0; i < n; ++i) by_category[records[i].category].push_back(static_cast<int>(i));
        std::cout << "카테고리 " << by_category.size() << "종\n\n";

        // embeddings/ 안의 *.bin 파일을 모델로 간주 (JSON 파싱 없이 파일 스캔으로 발견)
        std::vector<fs::path> bin_files;
        for (auto& entry : fs::directory_iterator(emb_dir)) {
            if (entry.path().extension() == ".bin") bin_files.push_back(entry.path());
        }
        std::sort(bin_files.begin(), bin_files.end());
        if (bin_files.empty()) {
            std::cerr << "embeddings/*.bin 파일을 찾을 수 없습니다 (" << emb_dir << ")\n";
            return 1;
        }

        std::vector<ModelResult> results;

        for (auto& bin_path : bin_files) {
            std::string model_name = bin_path.stem().string();
            std::cout << "--- " << model_name << " ---\n";

            int dim = 0;
            std::vector<float> emb = load_embeddings(bin_path, n, dim);
            std::cout << "  차원: " << dim << "\n";

            auto at = [&](int idx) { return emb.data() + static_cast<size_t>(idx) * dim; };

            // 전체 쌍(i<j) 통계 — 지배적 비용 O(n^2 * dim)
            // 같은 루프에서 상위/하위 kPairReportSize개 유사도 쌍도 함께 추적한다
            // (top: 작은 값부터 나오는 min-heap을 K개로 유지 → 결과적으로 가장 큰 K개,
            //  bottom: 큰 값부터 나오는 max-heap을 K개로 유지 → 결과적으로 가장 작은 K개).
            Stats total;
            std::priority_queue<PairSim, std::vector<PairSim>, std::greater<PairSim>> top_heap;
            std::priority_queue<PairSim, std::vector<PairSim>, std::less<PairSim>> bottom_heap;
            for (size_t i = 0; i < n; ++i) {
                const float* vi = at(static_cast<int>(i));
                for (size_t j = i + 1; j < n; ++j) {
                    double sim = dot(vi, at(static_cast<int>(j)), dim);
                    total.add(sim);

                    top_heap.push(PairSim{sim, static_cast<int>(i), static_cast<int>(j)});
                    if (top_heap.size() > kPairReportSize) top_heap.pop();

                    bottom_heap.push(PairSim{sim, static_cast<int>(i), static_cast<int>(j)});
                    if (bottom_heap.size() > kPairReportSize) bottom_heap.pop();
                }
            }

            std::vector<PairSim> top_pairs, bottom_pairs;
            while (!top_heap.empty()) { top_pairs.push_back(top_heap.top()); top_heap.pop(); }
            while (!bottom_heap.empty()) { bottom_pairs.push_back(bottom_heap.top()); bottom_heap.pop(); }
            std::sort(top_pairs.begin(), top_pairs.end(),
                      [](const PairSim& a, const PairSim& b) { return a.sim > b.sim; });  // 가장 유사한 것부터
            std::sort(bottom_pairs.begin(), bottom_pairs.end(),
                      [](const PairSim& a, const PairSim& b) { return a.sim < b.sim; });  // 가장 안 닮은 것부터

            auto csv_field = [](const std::string& s) {
                std::string out = "\"";
                for (char c : s) {
                    if (c == '"') out += '"';
                    out += c;
                }
                out += '"';
                return out;
            };
            auto write_pairs_csv = [&](const fs::path& path, const std::vector<PairSim>& pairs) {
                std::ofstream f(path);
                f << "similarity,id_a,category_a,id_b,category_b,same_category\n";
                for (auto& p : pairs) {
                    const Record& ra = records[p.i];
                    const Record& rb = records[p.j];
                    f << std::fixed << std::setprecision(6) << p.sim << ","
                      << csv_field(ra.id) << "," << csv_field(ra.category) << ","
                      << csv_field(rb.id) << "," << csv_field(rb.category) << ","
                      << (ra.category == rb.category ? "1" : "0") << "\n";
                }
            };
            write_pairs_csv(emb_dir / (model_name + "_top_pairs.csv"), top_pairs);
            write_pairs_csv(emb_dir / (model_name + "_bottom_pairs.csv"), bottom_pairs);
            std::cout << "  유사도 쌍 저장: " << model_name << "_top_pairs.csv / _bottom_pairs.csv (각 "
                      << kPairReportSize << "건)\n";

            // 카테고리 내부(intra) 쌍 통계 — 전체 대비 훨씬 작은 부분집합
            Stats intra;
            for (auto& [category, idxs] : by_category) {
                for (size_t a = 0; a < idxs.size(); ++a) {
                    const float* va = at(idxs[a]);
                    for (size_t b = a + 1; b < idxs.size(); ++b) {
                        intra.add(dot(va, at(idxs[b]), dim));
                    }
                }
            }

            Stats inter = total - intra;

            ModelResult r;
            r.name = model_name;
            r.dim = dim;
            r.intra = intra;
            r.inter = inter;
            results.push_back(r);

            std::cout << "  intra(같은 카테고리)  n=" << intra.count
                      << "  mean=" << std::fixed << std::setprecision(4) << intra.mean()
                      << "  std=" << intra.stddev() << "\n";
            std::cout << "  inter(다른 카테고리)  n=" << inter.count
                      << "  mean=" << inter.mean() << "  std=" << inter.stddev() << "\n";
            std::cout << "  gap(intra-inter)      " << r.gap() << "\n\n";
        }

        // gap 기준 내림차순 정렬 (카테고리 분리력이 좋은 모델 순)
        std::sort(results.begin(), results.end(),
                  [](const ModelResult& a, const ModelResult& b) { return a.gap() > b.gap(); });

        std::cout << "======================================================================\n";
        std::cout << "모델별 벤치마크 비교 (gap = intra_mean - inter_mean, 클수록 카테고리 분리력 좋음)\n";
        std::cout << "======================================================================\n";
        std::cout << std::left << std::setw(38) << "모델"
                  << std::right << std::setw(10) << "intra"
                  << std::setw(10) << "inter"
                  << std::setw(10) << "gap" << "\n";
        std::cout << std::string(68, '-') << "\n";
        for (auto& r : results) {
            std::cout << std::left << std::setw(38) << r.name
                       << std::right << std::fixed << std::setprecision(4)
                       << std::setw(10) << r.intra.mean()
                       << std::setw(10) << r.inter.mean()
                       << std::setw(10) << r.gap() << "\n";
        }
        std::cout << "\n최고 gap 모델: " << results.front().name << "\n";

        // CSV로도 저장
        fs::path out_csv = emb_dir / "benchmark_results.csv";
        std::ofstream out(out_csv);
        out << "model,dim,n_records,intra_n,intra_mean,intra_std,inter_n,inter_mean,inter_std,gap\n";
        for (auto& r : results) {
            out << r.name << "," << r.dim << "," << n << ","
                << r.intra.count << "," << r.intra.mean() << "," << r.intra.stddev() << ","
                << r.inter.count << "," << r.inter.mean() << "," << r.inter.stddev() << ","
                << r.gap() << "\n";
        }
        std::cout << "결과 저장: " << out_csv << "\n";

    } catch (const std::exception& e) {
        std::cerr << "오류: " << e.what() << "\n";
        return 1;
    }

    return 0;
}
