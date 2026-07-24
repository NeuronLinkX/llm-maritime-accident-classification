// STEP 3 — K-Means 군집화 + Elbow/Silhouette 기반 K 자동 탐색
//
// step_2_process에서 넘어온 ko-sroberta-sts 임베딩(from_step2/*.bin, meta.csv)을
// 입력으로 받아, 여러 K 후보에 대해 K-Means를 반복 수행하고 WCSS(Elbow)와
// 평균 실루엣 계수를 계산해 최적 K를 자동으로 찾는다. 최종적으로 채택된 K로
// 군집화한 결과(문서별 군집 번호)를 CSV로 출력한다.
//
// 이 반복적 수치 계산(centroid 갱신, 거리 계산, WCSS/실루엣 집계)은 STEP1/2와
// 같은 원칙에 따라 C++로 처리한다. 군집별 키워드 도출(형태소 분석)은 Python 몫이다.
//
// 임베딩은 이미 L2 정규화되어 있으므로 코사인 유사도 = 내적이고,
// 두 단위벡터 사이의 제곱 유클리드 거리는 dist^2 = 2 - 2*dot 로 바로 구해진다.

#include <algorithm>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <random>
#include <string>
#include <unordered_map>
#include <vector>

namespace fs = std::filesystem;

struct Record {
    std::string id;
    std::string category;
    std::string filename;
};

static std::vector<std::string> parse_csv_line(const std::string& line) {
    std::vector<std::string> fields;
    std::string cur;
    bool in_quotes = false;
    for (size_t i = 0; i < line.size(); ++i) {
        char c = line[i];
        if (in_quotes) {
            if (c == '"') {
                if (i + 1 < line.size() && line[i + 1] == '"') { cur += '"'; ++i; }
                else in_quotes = false;
            } else cur += c;
        } else {
            if (c == '"') in_quotes = true;
            else if (c == ',') { fields.push_back(cur); cur.clear(); }
            else cur += c;
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
    std::getline(f, line);  // 헤더: index,id,category,filename
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
        throw std::runtime_error(path.string() + ": 파일 크기가 레코드 수와 맞지 않습니다");
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

struct KMeansResult {
    std::vector<int> assign;
    std::vector<double> centroids;  // k x dim
    double wcss = 0.0;
};

// Lloyd's 알고리즘 + k-means++ 초기화. 단위벡터라 dist^2 = 2 - 2*dot.
static KMeansResult run_kmeans(const std::vector<float>& emb, size_t n, int dim, int k,
                                std::mt19937& rng, int max_iters = 200) {
    auto at = [&](int idx) { return emb.data() + static_cast<size_t>(idx) * dim; };

    std::vector<double> centroids(static_cast<size_t>(k) * dim);
    std::vector<double> min_d2(n, std::numeric_limits<double>::max());

    // k-means++ 초기화
    std::uniform_int_distribution<size_t> first_pick(0, n - 1);
    size_t first = first_pick(rng);
    for (int d = 0; d < dim; ++d) centroids[d] = at(static_cast<int>(first))[d];

    for (int c = 1; c < k; ++c) {
        // 각 점에서 "지금까지 뽑힌 centroid 중 가장 가까운 것"까지의 거리 갱신
        for (size_t i = 0; i < n; ++i) {
            const float* vi = at(static_cast<int>(i));
            const double* last_c = &centroids[static_cast<size_t>(c - 1) * dim];
            double dp = 0.0, cn2 = 0.0;
            for (int d = 0; d < dim; ++d) { dp += vi[d] * last_c[d]; cn2 += last_c[d] * last_c[d]; }
            double vn2 = 0.0;
            for (int d = 0; d < dim; ++d) vn2 += static_cast<double>(vi[d]) * vi[d];
            double d2 = vn2 - 2.0 * dp + cn2;
            if (d2 < 0.0) d2 = 0.0;
            if (d2 < min_d2[i]) min_d2[i] = d2;
        }
        std::discrete_distribution<size_t> weighted(min_d2.begin(), min_d2.end());
        size_t next = weighted(rng);
        const float* vnext = at(static_cast<int>(next));
        for (int d = 0; d < dim; ++d) centroids[static_cast<size_t>(c) * dim + d] = vnext[d];
    }

    std::vector<int> assign(n, -1);
    double wcss = 0.0;

    for (int iter = 0; iter < max_iters; ++iter) {
        bool changed = false;
        wcss = 0.0;

        // 배정 단계
        for (size_t i = 0; i < n; ++i) {
            const float* vi = at(static_cast<int>(i));
            double vn2 = 0.0;
            for (int d = 0; d < dim; ++d) vn2 += static_cast<double>(vi[d]) * vi[d];

            int best_c = 0;
            double best_d2 = std::numeric_limits<double>::max();
            for (int c = 0; c < k; ++c) {
                const double* cc = &centroids[static_cast<size_t>(c) * dim];
                double dp = 0.0, cn2 = 0.0;
                for (int d = 0; d < dim; ++d) { dp += vi[d] * cc[d]; cn2 += cc[d] * cc[d]; }
                double d2 = vn2 - 2.0 * dp + cn2;
                if (d2 < best_d2) { best_d2 = d2; best_c = c; }
            }
            if (assign[i] != best_c) { assign[i] = best_c; changed = true; }
            wcss += best_d2;
        }

        // 갱신 단계
        std::vector<double> sum(static_cast<size_t>(k) * dim, 0.0);
        std::vector<int> count(k, 0);
        for (size_t i = 0; i < n; ++i) {
            int c = assign[i];
            count[c]++;
            const float* vi = at(static_cast<int>(i));
            for (int d = 0; d < dim; ++d) sum[static_cast<size_t>(c) * dim + d] += vi[d];
        }
        for (int c = 0; c < k; ++c) {
            if (count[c] == 0) {
                // 빈 군집: 가장 WCSS 기여가 큰 점을 새 centroid로 이동(재분열)
                size_t farthest = 0;
                double best_d2 = -1.0;
                for (size_t i = 0; i < n; ++i) {
                    const float* vi = at(static_cast<int>(i));
                    const double* cc = &centroids[static_cast<size_t>(assign[i]) * dim];
                    double d2 = 0.0;
                    for (int d = 0; d < dim; ++d) { double diff = vi[d] - cc[d]; d2 += diff * diff; }
                    if (d2 > best_d2) { best_d2 = d2; farthest = i; }
                }
                const float* vf = at(static_cast<int>(farthest));
                for (int d = 0; d < dim; ++d) centroids[static_cast<size_t>(c) * dim + d] = vf[d];
                continue;
            }
            for (int d = 0; d < dim; ++d)
                centroids[static_cast<size_t>(c) * dim + d] = sum[static_cast<size_t>(c) * dim + d] / count[c];
        }

        if (!changed) break;
    }

    return KMeansResult{assign, centroids, wcss};
}

static KMeansResult best_of_restarts(const std::vector<float>& emb, size_t n, int dim, int k,
                                      int restarts, unsigned seed_base) {
    KMeansResult best;
    best.wcss = std::numeric_limits<double>::max();
    for (int r = 0; r < restarts; ++r) {
        std::mt19937 rng(seed_base + static_cast<unsigned>(r) * 977u + static_cast<unsigned>(k) * 131u);
        KMeansResult res = run_kmeans(emb, n, dim, k, rng);
        if (res.wcss < best.wcss) best = std::move(res);
    }
    return best;
}

// 정규화된 K vs WCSS 곡선에서 첫점-끝점을 잇는 직선으로부터 수직거리가 가장 먼 지점을 elbow로 본다.
static int find_elbow(const std::vector<int>& ks, const std::vector<double>& wcss) {
    size_t n = ks.size();
    if (n < 3) return ks.front();
    double x1 = ks.front(), y1 = wcss.front();
    double x2 = ks.back(), y2 = wcss.back();
    double best_dist = -1.0;
    int best_k = ks.front();
    double line_len = std::sqrt((x2 - x1) * (x2 - x1) + (y2 - y1) * (y2 - y1));
    for (size_t i = 0; i < n; ++i) {
        double x0 = ks[i], y0 = wcss[i];
        double dist = std::abs((y2 - y1) * x0 - (x2 - x1) * y0 + x2 * y1 - y2 * x1) / line_len;
        if (dist > best_dist) { best_dist = dist; best_k = ks[i]; }
    }
    return best_k;
}

int main(int argc, char** argv) {
    fs::path in_dir = (argc > 1) ? fs::path(argv[1]) : fs::path("../from_step2");
    fs::path out_dir = (argc > 2) ? fs::path(argv[2]) : fs::path("../output");
    int k_min = (argc > 3) ? std::atoi(argv[3]) : 2;
    int k_max = (argc > 4) ? std::atoi(argv[4]) : 30;
    int restarts = (argc > 5) ? std::atoi(argv[5]) : 10;

    try {
        fs::create_directories(out_dir);

        auto records = load_meta(in_dir / "meta.csv");
        size_t n = records.size();
        std::cout << "메타데이터 " << n << "건 로드\n";

        // in_dir 안의 첫 *.bin 파일을 임베딩으로 사용 (from_step2에는 선택된 모델 하나만 넘어온다)
        fs::path bin_path;
        for (auto& entry : fs::directory_iterator(in_dir)) {
            if (entry.path().extension() == ".bin") { bin_path = entry.path(); break; }
        }
        if (bin_path.empty()) throw std::runtime_error("from_step2/*.bin 임베딩 파일을 찾을 수 없습니다");

        int dim = 0;
        std::vector<float> emb = load_embeddings(bin_path, n, dim);
        std::cout << "임베딩: " << bin_path.filename() << " (차원 " << dim << ")\n";

        auto at = [&](int idx) { return emb.data() + static_cast<size_t>(idx) * dim; };

        // 실루엣 계산에 재사용할 전체 쌍 거리(유클리드, 단위벡터라 dist=sqrt(2-2*dot))를 한 번만 계산
        std::vector<float> dist(n * n, 0.0f);
        for (size_t i = 0; i < n; ++i) {
            const float* vi = at(static_cast<int>(i));
            for (size_t j = i + 1; j < n; ++j) {
                double d2 = 2.0 - 2.0 * dot(vi, at(static_cast<int>(j)), dim);
                if (d2 < 0.0) d2 = 0.0;
                float d = static_cast<float>(std::sqrt(d2));
                dist[i * n + j] = d;
                dist[j * n + i] = d;
            }
        }

        std::cout << "\nK 탐색 범위: " << k_min << " ~ " << k_max << " (restarts=" << restarts << ")\n";
        std::cout << std::left << std::setw(6) << "K" << std::right << std::setw(14) << "WCSS"
                  << std::setw(14) << "avg_silhouette" << "\n";

        std::vector<int> ks;
        std::vector<double> wcss_list;
        std::vector<double> sil_list;
        std::vector<KMeansResult> all_results;

        for (int k = k_min; k <= k_max; ++k) {
            KMeansResult res = best_of_restarts(emb, n, dim, k, restarts, 42u);

            // 평균 실루엣 계수: a(i) = 같은 군집 내 평균거리, b(i) = 가장 가까운 다른 군집까지 평균거리
            std::vector<std::vector<int>> members(k);
            for (size_t i = 0; i < n; ++i) members[res.assign[i]].push_back(static_cast<int>(i));

            double sil_sum = 0.0;
            int sil_count = 0;
            for (size_t i = 0; i < n; ++i) {
                int ci = res.assign[i];
                auto& same = members[ci];
                double a = 0.0;
                if (same.size() > 1) {
                    for (int j : same) if (j != static_cast<int>(i)) a += dist[i * n + j];
                    a /= static_cast<double>(same.size() - 1);
                } else {
                    continue;  // 군집 크기 1이면 실루엣 정의상 0으로 처리(스킵 후 평균에서 제외)
                }
                double b = std::numeric_limits<double>::max();
                for (int c = 0; c < k; ++c) {
                    if (c == ci || members[c].empty()) continue;
                    double sum = 0.0;
                    for (int j : members[c]) sum += dist[i * n + j];
                    double mean_d = sum / static_cast<double>(members[c].size());
                    if (mean_d < b) b = mean_d;
                }
                double s = (b - a) / std::max(a, b);
                sil_sum += s;
                sil_count++;
            }
            double avg_sil = sil_count > 0 ? sil_sum / sil_count : 0.0;

            ks.push_back(k);
            wcss_list.push_back(res.wcss);
            sil_list.push_back(avg_sil);
            all_results.push_back(std::move(res));

            std::cout << std::left << std::setw(6) << k << std::right << std::fixed << std::setprecision(2)
                      << std::setw(14) << wcss_list.back() << std::setprecision(4)
                      << std::setw(14) << avg_sil << "\n";
        }

        int elbow_k = find_elbow(ks, wcss_list);
        int silhouette_k = ks[std::max_element(sil_list.begin(), sil_list.end()) - sil_list.begin()];

        std::cout << "\nElbow 기준 K = " << elbow_k << "\n";
        std::cout << "Silhouette 기준 K = " << silhouette_k << " (최고 평균 실루엣 "
                  << std::fixed << std::setprecision(4) << *std::max_element(sil_list.begin(), sil_list.end()) << ")\n";

        int chosen_k = silhouette_k;
        std::cout << "채택 K = " << chosen_k
                  << (elbow_k == silhouette_k ? " (Elbow와 Silhouette 일치)" : " (Silhouette 우선 채택, Elbow는 참고용으로 함께 기록)")
                  << "\n";

        // K 탐색 결과 저장
        {
            std::ofstream f(out_dir / "k_selection.csv");
            f << "k,wcss,avg_silhouette,is_elbow,is_silhouette_best,is_chosen\n";
            for (size_t idx = 0; idx < ks.size(); ++idx) {
                f << ks[idx] << "," << std::fixed << std::setprecision(6) << wcss_list[idx] << ","
                  << sil_list[idx] << ","
                  << (ks[idx] == elbow_k ? 1 : 0) << ","
                  << (ks[idx] == silhouette_k ? 1 : 0) << ","
                  << (ks[idx] == chosen_k ? 1 : 0) << "\n";
            }
        }

        // 채택된 K의 최종 군집 결과 저장 (더 많은 restarts로 한 번 더 안정화)
        KMeansResult final_res = best_of_restarts(emb, n, dim, chosen_k, restarts * 2, 1337u);
        {
            std::ofstream f(out_dir / "clusters.csv");
            f << "index,id,category,filename,cluster\n";
            for (size_t i = 0; i < n; ++i) {
                auto esc = [](const std::string& s) {
                    std::string out = "\"";
                    for (char c : s) { if (c == '"') out += '"'; out += c; }
                    out += '"';
                    return out;
                };
                f << i << "," << esc(records[i].id) << "," << esc(records[i].category) << ","
                  << esc(records[i].filename) << "," << final_res.assign[i] << "\n";
            }
        }

        // 군집별 크기 및 원래 STEP1 카테고리 분포(교차검증용) 출력
        std::vector<std::vector<int>> members(chosen_k);
        for (size_t i = 0; i < n; ++i) members[final_res.assign[i]].push_back(static_cast<int>(i));
        std::cout << "\n=== 채택 K=" << chosen_k << " 군집 요약 ===\n";
        for (int c = 0; c < chosen_k; ++c) {
            std::unordered_map<std::string, int> cat_count;
            for (int i : members[c]) cat_count[records[i].category]++;
            std::vector<std::pair<std::string, int>> sorted_cats(cat_count.begin(), cat_count.end());
            std::sort(sorted_cats.begin(), sorted_cats.end(),
                      [](auto& a, auto& b) { return a.second > b.second; });
            std::cout << "군집 " << c << " (n=" << members[c].size() << "): ";
            for (size_t idx = 0; idx < sorted_cats.size() && idx < 3; ++idx) {
                std::cout << sorted_cats[idx].first << "(" << sorted_cats[idx].second << ") ";
            }
            std::cout << "\n";
        }

        std::cout << "\n결과 저장: " << out_dir / "k_selection.csv" << ", " << out_dir / "clusters.csv" << "\n";

    } catch (const std::exception& e) {
        std::cerr << "오류: " << e.what() << "\n";
        return 1;
    }

    return 0;
}
