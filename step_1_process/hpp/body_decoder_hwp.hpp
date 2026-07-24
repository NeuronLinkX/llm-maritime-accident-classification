#pragma once

#include "body_decoder_common.hpp"

namespace body_decoder {

// CFBF 디렉터리 엔트리 한 개를 표현한다.
struct CfbDirectoryEntry {
    std::string name;
    uint8_t type = 0;
    uint32_t left = CFB_NOSTREAM;
    uint32_t right = CFB_NOSTREAM;
    uint32_t child = CFB_NOSTREAM;
    uint32_t start_sector = CFB_ENDOFCHAIN;
    uint64_t size = 0;
};

// HWP 5.x OLE2/CFBF 컨테이너를 직접 읽는 최소 리더다.
class CompoundFile {
public:
    // 파일 전체 바이트를 받아 헤더/FAT/디렉터리/MiniFAT를 한 번에 파싱한다.
    explicit CompoundFile(std::vector<uint8_t> bytes)
        : bytes_(std::move(bytes)) {
        parse_header();
        parse_fat();
        parse_directory();
        parse_minifat();
        build_stream_index();
    }

    // 지정한 경로의 스트림이 존재하는지 검사한다.
    bool has_stream(const std::string &path) const {
        return stream_paths_.find(lower_ascii(path)) != stream_paths_.end();
    }

    // 스트림 크기에 따라 일반 FAT 또는 MiniFAT를 따라 내용을 읽는다.
    std::vector<uint8_t> read_stream(const std::string &path) const {
        auto it = stream_paths_.find(lower_ascii(path));
        if (it == stream_paths_.end()) {
            throw std::runtime_error("CFBF stream not found: " + path);
        }
        const auto &entry = directory_.at(it->second);
        if (entry.size == 0) return {};
        if (entry.size < mini_stream_cutoff_) {
            return read_mini_chain(entry.start_sector, entry.size);
        }
        return read_regular_chain(entry.start_sector, entry.size);
    }

    // 컨테이너 안의 모든 스트림 경로를 정렬해 반환한다.
    std::vector<std::string> stream_paths() const {
        std::vector<std::string> result;
        result.reserve(stream_paths_.size());
        for (const auto &kv : stream_paths_) result.push_back(kv.first);
        std::sort(result.begin(), result.end());
        return result;
    }

private:
    std::vector<uint8_t> bytes_;
    uint16_t major_version_ = 0;
    size_t sector_size_ = 0;
    size_t mini_sector_size_ = 0;
    uint32_t first_dir_sector_ = CFB_ENDOFCHAIN;
    uint32_t mini_stream_cutoff_ = 4096;
    uint32_t first_minifat_sector_ = CFB_ENDOFCHAIN;
    uint32_t num_minifat_sectors_ = 0;
    uint32_t first_difat_sector_ = CFB_ENDOFCHAIN;
    uint32_t num_difat_sectors_ = 0;
    uint32_t num_fat_sectors_ = 0;

    std::vector<uint32_t> difat_;
    std::vector<uint32_t> fat_;
    std::vector<uint32_t> minifat_;
    std::vector<CfbDirectoryEntry> directory_;
    std::vector<uint8_t> root_mini_stream_;
    std::unordered_map<std::string, size_t> stream_paths_;

    // 섹터 번호를 실제 파일 오프셋으로 변환한다.
    size_t sector_offset(uint32_t sector) const {
        if (sector > (std::numeric_limits<size_t>::max() / sector_size_) - 1) {
            throw std::runtime_error("CFBF sector offset overflow");
        }
        size_t offset = (static_cast<size_t>(sector) + 1) * sector_size_;
        if (offset > bytes_.size()) {
            throw std::runtime_error("CFBF sector offset out of file bounds");
        }
        return offset;
    }

    // CFBF 헤더 시그니처와 섹터 구성을 검증하고 주요 포인터를 읽는다.
    void parse_header() {
        static constexpr uint8_t signature[8] = {
            0xD0, 0xCF, 0x11, 0xE0, 0xA1, 0xB1, 0x1A, 0xE1
        };
        if (bytes_.size() < 512 ||
            std::memcmp(bytes_.data(), signature, sizeof(signature)) != 0) {
            throw std::runtime_error("not a valid OLE2/CFBF file");
        }

        const uint16_t byte_order = rd_u16(bytes_.data() + 0x1C);
        if (byte_order != 0xFFFE) {
            throw std::runtime_error("unsupported CFBF byte order");
        }

        major_version_ = rd_u16(bytes_.data() + 0x1A);
        const uint16_t sector_shift = rd_u16(bytes_.data() + 0x1E);
        const uint16_t mini_sector_shift = rd_u16(bytes_.data() + 0x20);
        if (sector_shift < 9 || sector_shift > 12 || mini_sector_shift > 12) {
            throw std::runtime_error("invalid CFBF sector geometry");
        }
        sector_size_ = static_cast<size_t>(1u) << sector_shift;
        mini_sector_size_ = static_cast<size_t>(1u) << mini_sector_shift;

        num_fat_sectors_ = rd_u32(bytes_.data() + 0x2C);
        first_dir_sector_ = rd_u32(bytes_.data() + 0x30);
        mini_stream_cutoff_ = rd_u32(bytes_.data() + 0x38);
        first_minifat_sector_ = rd_u32(bytes_.data() + 0x3C);
        num_minifat_sectors_ = rd_u32(bytes_.data() + 0x40);
        first_difat_sector_ = rd_u32(bytes_.data() + 0x44);
        num_difat_sectors_ = rd_u32(bytes_.data() + 0x48);

        for (size_t i = 0; i < 109; ++i) {
            uint32_t sid = rd_u32(bytes_.data() + 0x4C + i * 4);
            if (sid != CFB_FREESECT) difat_.push_back(sid);
        }
    }

    // DIFAT를 따라 전체 FAT 섹터 목록을 읽고 메모리 인덱스로 펼친다.
    void parse_fat() {
        uint32_t difat_sector = first_difat_sector_;
        const size_t entries_per_difat_sector = sector_size_ / 4 - 1;
        std::unordered_set<uint32_t> seen_difat;

        for (uint32_t i = 0; i < num_difat_sectors_; ++i) {
            if (difat_sector == CFB_ENDOFCHAIN || difat_sector == CFB_FREESECT) {
                throw std::runtime_error("truncated CFBF DIFAT chain");
            }
            if (!seen_difat.insert(difat_sector).second) {
                throw std::runtime_error("cycle in CFBF DIFAT chain");
            }
            size_t offset = sector_offset(difat_sector);
            if (offset + sector_size_ > bytes_.size()) {
                throw std::runtime_error("CFBF DIFAT sector out of bounds");
            }
            for (size_t j = 0; j < entries_per_difat_sector; ++j) {
                uint32_t sid = rd_u32(bytes_.data() + offset + j * 4);
                if (sid != CFB_FREESECT) difat_.push_back(sid);
            }
            difat_sector = rd_u32(bytes_.data() + offset +
                                  entries_per_difat_sector * 4);
        }

        if (difat_.size() < num_fat_sectors_) {
            throw std::runtime_error("CFBF DIFAT lists fewer FAT sectors than header");
        }

        const size_t entries_per_fat_sector = sector_size_ / 4;
        fat_.reserve(static_cast<size_t>(num_fat_sectors_) * entries_per_fat_sector);
        for (uint32_t i = 0; i < num_fat_sectors_; ++i) {
            uint32_t fat_sector = difat_[i];
            size_t offset = sector_offset(fat_sector);
            if (offset + sector_size_ > bytes_.size()) {
                throw std::runtime_error("CFBF FAT sector out of bounds");
            }
            for (size_t j = 0; j < entries_per_fat_sector; ++j) {
                fat_.push_back(rd_u32(bytes_.data() + offset + j * 4));
            }
        }
    }

    // 일반 FAT 체인을 따라 지정 크기만큼 스트림을 복원한다.
    std::vector<uint8_t> read_regular_chain(uint32_t start_sector,
                                            uint64_t requested_size) const {
        if (requested_size == 0) return {};
        if (requested_size > static_cast<uint64_t>(std::numeric_limits<size_t>::max())) {
            throw std::runtime_error("CFBF stream is too large for this process");
        }

        std::vector<uint8_t> out;
        out.reserve(static_cast<size_t>(requested_size));
        std::unordered_set<uint32_t> seen;
        uint32_t sid = start_sector;

        while (sid != CFB_ENDOFCHAIN && out.size() < requested_size) {
            if (sid == CFB_FREESECT || sid == CFB_FATSECT || sid == CFB_DIFSECT ||
                sid >= fat_.size()) {
                throw std::runtime_error("invalid sector id in CFBF FAT chain");
            }
            if (!seen.insert(sid).second) {
                throw std::runtime_error("cycle in CFBF FAT chain");
            }
            size_t offset = sector_offset(sid);
            if (offset + sector_size_ > bytes_.size()) {
                throw std::runtime_error("CFBF stream sector out of bounds");
            }
            size_t remaining = static_cast<size_t>(requested_size) - out.size();
            size_t take = std::min(sector_size_, remaining);
            out.insert(out.end(), bytes_.begin() + static_cast<ptrdiff_t>(offset),
                       bytes_.begin() + static_cast<ptrdiff_t>(offset + take));
            sid = fat_[sid];
        }

        if (out.size() != requested_size) {
            throw std::runtime_error("CFBF FAT chain ended before declared stream size");
        }
        return out;
    }

    // 디렉터리 스트림을 읽어 엔트리 배열을 만들고 루트 mini stream을 캐시한다.
    void parse_directory() {
        if (first_dir_sector_ == CFB_ENDOFCHAIN) {
            throw std::runtime_error("CFBF has no directory stream");
        }

        std::vector<uint8_t> dir_bytes;
        std::unordered_set<uint32_t> seen;
        uint32_t sid = first_dir_sector_;
        while (sid != CFB_ENDOFCHAIN) {
            if (sid >= fat_.size() || !seen.insert(sid).second) {
                throw std::runtime_error("invalid or cyclic CFBF directory chain");
            }
            size_t offset = sector_offset(sid);
            if (offset + sector_size_ > bytes_.size()) {
                throw std::runtime_error("CFBF directory sector out of bounds");
            }
            dir_bytes.insert(dir_bytes.end(),
                             bytes_.begin() + static_cast<ptrdiff_t>(offset),
                             bytes_.begin() + static_cast<ptrdiff_t>(offset + sector_size_));
            sid = fat_[sid];
        }

        for (size_t pos = 0; pos + 128 <= dir_bytes.size(); pos += 128) {
            const uint8_t *p = dir_bytes.data() + pos;
            uint16_t name_bytes = rd_u16(p + 64);
            if (name_bytes > 64 || (name_bytes % 2) != 0) name_bytes = 0;

            CfbDirectoryEntry e;
            if (name_bytes >= 2) {
                e.name = utf16le_to_utf8(p, name_bytes - 2, false);
            }
            e.type = p[66];
            e.left = rd_u32(p + 68);
            e.right = rd_u32(p + 72);
            e.child = rd_u32(p + 76);
            e.start_sector = rd_u32(p + 116);
            e.size = rd_u64(p + 120);
            if (major_version_ == 3) e.size &= 0xFFFFFFFFull;
            directory_.push_back(std::move(e));
        }

        if (directory_.empty() || directory_[0].type != 5) {
            throw std::runtime_error("CFBF root directory entry is missing");
        }

        const auto &root = directory_[0];
        if (root.size > 0) {
            root_mini_stream_ = read_regular_chain(root.start_sector, root.size);
        }
    }

    // 작은 스트림 해석에 필요한 MiniFAT 체인을 로드한다.
    void parse_minifat() {
        if (num_minifat_sectors_ == 0 ||
            first_minifat_sector_ == CFB_ENDOFCHAIN) {
            return;
        }

        uint64_t bytes_needed = static_cast<uint64_t>(num_minifat_sectors_) *
                                static_cast<uint64_t>(sector_size_);
        std::vector<uint8_t> raw =
            read_regular_chain(first_minifat_sector_, bytes_needed);
        minifat_.reserve(raw.size() / 4);
        for (size_t i = 0; i + 4 <= raw.size(); i += 4) {
            minifat_.push_back(rd_u32(raw.data() + i));
        }
    }

    // MiniFAT 체인을 따라 작은 스트림을 root mini stream 안에서 복원한다.
    std::vector<uint8_t> read_mini_chain(uint32_t start_sector,
                                         uint64_t requested_size) const {
        if (requested_size == 0) return {};
        if (minifat_.empty() || root_mini_stream_.empty()) {
            throw std::runtime_error("CFBF mini stream requested but MiniFAT is absent");
        }
        if (requested_size > static_cast<uint64_t>(std::numeric_limits<size_t>::max())) {
            throw std::runtime_error("CFBF mini stream is too large");
        }

        std::vector<uint8_t> out;
        out.reserve(static_cast<size_t>(requested_size));
        std::unordered_set<uint32_t> seen;
        uint32_t sid = start_sector;

        while (sid != CFB_ENDOFCHAIN && out.size() < requested_size) {
            if (sid >= minifat_.size() || !seen.insert(sid).second) {
                throw std::runtime_error("invalid or cyclic CFBF MiniFAT chain");
            }
            size_t offset = static_cast<size_t>(sid) * mini_sector_size_;
            if (offset + mini_sector_size_ > root_mini_stream_.size()) {
                throw std::runtime_error("CFBF mini sector out of root mini stream bounds");
            }
            size_t remaining = static_cast<size_t>(requested_size) - out.size();
            size_t take = std::min(mini_sector_size_, remaining);
            out.insert(out.end(),
                       root_mini_stream_.begin() + static_cast<ptrdiff_t>(offset),
                       root_mini_stream_.begin() + static_cast<ptrdiff_t>(offset + take));
            sid = minifat_[sid];
        }

        if (out.size() != requested_size) {
            throw std::runtime_error("CFBF MiniFAT chain ended before stream size");
        }
        return out;
    }

    // 디렉터리 트리를 순회해 `경로 -> 엔트리 인덱스` 매핑을 구성한다.
    void build_stream_index() {
        std::unordered_set<uint64_t> visiting;

        std::function<void(uint32_t, const std::string &)> walk_tree;
        walk_tree = [&](uint32_t id, const std::string &parent) {
            if (id == CFB_NOSTREAM) return;
            if (id >= directory_.size()) {
                throw std::runtime_error("CFBF directory tree references invalid entry");
            }

            uint64_t key = (static_cast<uint64_t>(id) << 32) ^
                           static_cast<uint64_t>(std::hash<std::string>{}(parent));
            if (!visiting.insert(key).second) {
                throw std::runtime_error("cycle in CFBF directory red-black tree");
            }

            const auto &e = directory_[id];
            walk_tree(e.left, parent);

            std::string full = parent.empty() ? e.name : parent + "/" + e.name;
            if (e.type == 2) {
                stream_paths_[lower_ascii(full)] = id;
            } else if (e.type == 1) {
                walk_tree(e.child, full);
            }

            walk_tree(e.right, parent);
            visiting.erase(key);
        };

        walk_tree(directory_[0].child, "");
    }
};

// HWP 문단 안의 확장 제어문자인지 판정한다.
bool is_extended_control(uint16_t ch) {
    switch (ch) {
        case 1: case 2: case 3: case 4: case 5: case 6: case 7: case 8:
        case 11: case 12:
        case 14: case 15: case 16: case 17: case 18: case 19: case 20:
        case 21: case 22: case 23:
            return true;
        default:
            return false;
    }
}

// HWPTAG_PARA_TEXT payload를 읽어 제어문자를 정리한 UTF-8 문장으로 변환한다.
std::string decode_hwp_para_text(const uint8_t *data, size_t len) {
    std::vector<uint16_t> units;
    units.reserve(len / 2);
    for (size_t i = 0; i + 1 < len; i += 2) {
        units.push_back(rd_u16(data + i));
    }

    std::vector<uint16_t> filtered;
    filtered.reserve(units.size());
    size_t i = 0;
    while (i < units.size()) {
        uint16_t ch = units[i];
        if (ch == 0) {
            ++i;
        } else if (ch == 9) {
            filtered.push_back('\t');
            ++i;
        } else if (ch == 10 || ch == 13) {
            filtered.push_back('\n');
            ++i;
        } else if (ch < 32) {
            if (is_extended_control(ch)) {
                i = std::min(units.size(), i + 8);
            } else {
                ++i;
            }
        } else {
            filtered.push_back(ch);
            ++i;
        }
    }

    std::string out;
    for (size_t j = 0; j < filtered.size(); ++j) {
        uint16_t w1 = filtered[j];
        uint32_t cp = w1;
        if (w1 >= 0xD800 && w1 <= 0xDBFF && j + 1 < filtered.size()) {
            uint16_t w2 = filtered[j + 1];
            if (w2 >= 0xDC00 && w2 <= 0xDFFF) {
                ++j;
                cp = 0x10000u +
                    ((static_cast<uint32_t>(w1) - 0xD800u) << 10) +
                    (static_cast<uint32_t>(w2) - 0xDC00u);
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

// 압축 해제된 HWP Section 레코드 스트림을 순회해 본문 문단만 추출한다.
void parse_hwp_section_records(const std::vector<uint8_t> &buf,
                               DecodeResult &result) {
    constexpr int HWPTAG_PARA_TEXT = 67;
    size_t pos = 0;
    while (pos + 4 <= buf.size()) {
        uint32_t header = rd_u32(buf.data() + pos);
        pos += 4;

        int tag_id = static_cast<int>(header & 0x3FFu);
        int level = static_cast<int>((header >> 10) & 0x3FFu);
        uint32_t size = (header >> 20) & 0xFFFu;
        if (size == 0xFFFu) {
            if (pos + 4 > buf.size()) {
                throw std::runtime_error("truncated extended HWP record header");
            }
            size = rd_u32(buf.data() + pos);
            pos += 4;
        }
        if (size > buf.size() - pos) {
            throw std::runtime_error("HWP record payload exceeds section stream");
        }

        if (tag_id == HWPTAG_PARA_TEXT && size > 0) {
            std::string text = decode_hwp_para_text(buf.data() + pos, size);
            if (!trim_copy(text).empty()) {
                result.paragraphs.push_back({level, tag_id, text});
            }
        }
        pos += size;
        ++result.record_count;
    }
}

// HWP 5.x 파일 전체를 디코딩한다.
// FileHeader를 확인하고 BodyText/SectionN을 번호순으로 읽어 문단을 만든다.
DecodeResult decode_hwp(const std::string &input_path,
                        const Options & /*options*/) {
    CompoundFile cfb(read_file(input_path));
    if (!cfb.has_stream("FileHeader")) {
        throw std::runtime_error("HWP FileHeader stream is missing");
    }

    std::vector<uint8_t> file_header = cfb.read_stream("FileHeader");
    if (file_header.size() < 40) {
        throw std::runtime_error("HWP FileHeader stream is truncated");
    }
    const std::string signature(reinterpret_cast<const char *>(file_header.data()),
                                std::min<size_t>(32, file_header.size()));
    if (signature.find("HWP Document File") == std::string::npos) {
        throw std::runtime_error("OLE2 file is not an HWP 5.x document");
    }

    uint32_t properties = rd_u32(file_header.data() + 36);
    bool compressed = (properties & 0x1u) != 0;
    bool encrypted = (properties & 0x2u) != 0;
    bool distribution = (properties & 0x4u) != 0;
    if (encrypted || distribution) {
        throw std::runtime_error(
            "encrypted/distribution HWP is unsupported without decryption");
    }

    struct SectionPath { int index; std::string path; };
    std::vector<SectionPath> sections;
    for (const std::string &lower_path : cfb.stream_paths()) {
        const std::string prefix = "bodytext/section";
        if (!starts_with(lower_path, prefix)) continue;
        std::string suffix = lower_path.substr(prefix.size());
        if (suffix.empty() ||
            !std::all_of(suffix.begin(), suffix.end(), [](unsigned char c) {
                return std::isdigit(c) != 0;
            })) {
            continue;
        }
        sections.push_back({std::stoi(suffix), lower_path});
    }
    std::sort(sections.begin(), sections.end(),
              [](const SectionPath &a, const SectionPath &b) {
                  return a.index < b.index;
              });
    if (sections.empty()) {
        throw std::runtime_error("HWP has no BodyText/SectionN streams");
    }

    DecodeResult result;
    result.format = "hwp";
    result.extraction_mode = "hwp_cfbf_native";

    for (const auto &section : sections) {
        std::vector<uint8_t> raw = cfb.read_stream(section.path);
        std::vector<uint8_t> body = compressed ? raw_inflate(raw) : std::move(raw);
        result.decompressed_bytes += body.size();
        parse_hwp_section_records(body, result);
    }

    result.chosen_metrics = evaluate_text(paragraphs_to_text(result.paragraphs));
    result.native_metrics = result.chosen_metrics;
    return result;
}

}  // namespace body_decoder
