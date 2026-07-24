#pragma once

#include "body_decoder_common.hpp"

namespace body_decoder {

// ZIP 중앙 디렉터리 엔트리 중 필요한 필드만 보관하는 구조체다.
struct ZipEntry {
    std::string name;
    uint16_t method = 0;
    uint32_t comp_size = 0;
    uint32_t uncomp_size = 0;
    uint32_t local_header_offset = 0;
};

// ZIP 파일 끝의 EOCD를 찾아 중앙 디렉터리 전체를 읽는다.
std::vector<ZipEntry> read_zip_central_directory(const std::vector<uint8_t> &buf) {
    constexpr uint32_t EOCD_SIG = 0x06054B50u;
    constexpr uint32_t CD_SIG = 0x02014B50u;
    constexpr size_t EOCD_MIN_LEN = 22;

    if (buf.size() < EOCD_MIN_LEN) {
        throw std::runtime_error("file too small to be ZIP/HWPX");
    }

    size_t search_start =
        buf.size() > EOCD_MIN_LEN + 65535
            ? buf.size() - EOCD_MIN_LEN - 65535
            : 0;
    std::optional<size_t> eocd_pos;
    for (size_t pos = buf.size() - EOCD_MIN_LEN + 1; pos-- > search_start;) {
        if (rd_u32(buf.data() + pos) == EOCD_SIG) {
            eocd_pos = pos;
            break;
        }
        if (pos == 0) break;
    }
    if (!eocd_pos) {
        throw std::runtime_error("ZIP EOCD record not found");
    }

    uint16_t disk_no = rd_u16(buf.data() + *eocd_pos + 4);
    uint16_t cd_disk = rd_u16(buf.data() + *eocd_pos + 6);
    if (disk_no != 0 || cd_disk != 0) {
        throw std::runtime_error("multi-disk ZIP/HWPX is unsupported");
    }

    uint16_t entry_count = rd_u16(buf.data() + *eocd_pos + 10);
    uint32_t cd_offset = rd_u32(buf.data() + *eocd_pos + 16);
    size_t pos = cd_offset;
    std::vector<ZipEntry> entries;
    entries.reserve(entry_count);

    for (uint16_t i = 0; i < entry_count; ++i) {
        if (pos + 46 > buf.size() || rd_u32(buf.data() + pos) != CD_SIG) {
            throw std::runtime_error("malformed ZIP central directory");
        }
        ZipEntry e;
        e.method = rd_u16(buf.data() + pos + 10);
        e.comp_size = rd_u32(buf.data() + pos + 20);
        e.uncomp_size = rd_u32(buf.data() + pos + 24);
        uint16_t name_len = rd_u16(buf.data() + pos + 28);
        uint16_t extra_len = rd_u16(buf.data() + pos + 30);
        uint16_t comment_len = rd_u16(buf.data() + pos + 32);
        e.local_header_offset = rd_u32(buf.data() + pos + 42);

        size_t name_pos = pos + 46;
        size_t next = name_pos + name_len + extra_len + comment_len;
        if (next > buf.size()) {
            throw std::runtime_error("ZIP central directory entry out of bounds");
        }
        e.name.assign(reinterpret_cast<const char *>(buf.data() + name_pos),
                      name_len);
        entries.push_back(std::move(e));
        pos = next;
    }
    return entries;
}

// 중앙 디렉터리 엔트리 정보를 사용해 실제 로컬 파일 엔트리를 복원한다.
std::vector<uint8_t> extract_zip_entry(const std::vector<uint8_t> &buf,
                                       const ZipEntry &entry) {
    constexpr uint32_t LOCAL_SIG = 0x04034B50u;
    size_t pos = entry.local_header_offset;
    if (pos + 30 > buf.size() || rd_u32(buf.data() + pos) != LOCAL_SIG) {
        throw std::runtime_error("malformed ZIP local header: " + entry.name);
    }
    uint16_t name_len = rd_u16(buf.data() + pos + 26);
    uint16_t extra_len = rd_u16(buf.data() + pos + 28);
    size_t data_start = pos + 30 + name_len + extra_len;
    if (data_start > buf.size() || entry.comp_size > buf.size() - data_start) {
        throw std::runtime_error("ZIP entry data out of bounds: " + entry.name);
    }

    if (entry.method == 0) {
        return std::vector<uint8_t>(
            buf.begin() + static_cast<ptrdiff_t>(data_start),
            buf.begin() + static_cast<ptrdiff_t>(data_start + entry.comp_size));
    }
    if (entry.method == 8) {
        return raw_inflate(buf.data() + data_start, entry.comp_size,
                           entry.uncomp_size);
    }
    throw std::runtime_error("unsupported ZIP compression method " +
                             std::to_string(entry.method) + " for " +
                             entry.name);
}

// XML 엔티티 참조를 실제 문자로 치환한다.
std::string xml_unescape(const std::string &s) {
    std::string out;
    out.reserve(s.size());
    for (size_t i = 0; i < s.size();) {
        if (s[i] == '&') {
            size_t semi = s.find(';', i + 1);
            if (semi != std::string::npos && semi - i <= 12) {
                std::string entity = s.substr(i + 1, semi - i - 1);
                if (entity == "amp") { out += '&'; i = semi + 1; continue; }
                if (entity == "lt") { out += '<'; i = semi + 1; continue; }
                if (entity == "gt") { out += '>'; i = semi + 1; continue; }
                if (entity == "quot") { out += '"'; i = semi + 1; continue; }
                if (entity == "apos") { out += '\''; i = semi + 1; continue; }
                if (!entity.empty() && entity[0] == '#') {
                    try {
                        uint32_t cp = 0;
                        if (entity.size() > 2 &&
                            (entity[1] == 'x' || entity[1] == 'X')) {
                            cp = static_cast<uint32_t>(
                                std::stoul(entity.substr(2), nullptr, 16));
                        } else {
                            cp = static_cast<uint32_t>(
                                std::stoul(entity.substr(1), nullptr, 10));
                        }
                        append_utf8(out, cp);
                        i = semi + 1;
                        continue;
                    } catch (...) {
                    }
                }
            }
        }
        out.push_back(s[i++]);
    }
    return out;
}

// 네임스페이스 접두사를 제거하고 로컬 태그명만 반환한다.
std::string local_tag_name(const std::string &raw) {
    size_t colon = raw.find(':');
    return colon == std::string::npos ? raw : raw.substr(colon + 1);
}

// HWPX section XML을 단일 패스로 스캔해 문단 텍스트를 추출한다.
// `<p>`, `<t>`, `<tbl>`, `<tab>`, `<lineBreak>` 중심으로만 해석한다.
void extract_hwpx_paragraphs(const std::string &xml,
                             std::vector<Paragraph> &out,
                             size_t &record_count) {
    size_t i = 0;
    bool in_paragraph = false;
    bool in_text = false;
    int table_depth = 0;
    std::string paragraph;

    auto flush = [&]() {
        if (in_paragraph && !trim_copy(paragraph).empty()) {
            out.push_back({std::max(0, table_depth), 67, paragraph});
        }
        paragraph.clear();
        in_paragraph = false;
    };

    while (i < xml.size()) {
        if (xml[i] != '<') {
            if (in_text) {
                size_t start = i;
                while (i < xml.size() && xml[i] != '<') ++i;
                paragraph += xml_unescape(xml.substr(start, i - start));
            } else {
                ++i;
            }
            continue;
        }

        if (xml.compare(i, 4, "<!--") == 0) {
            size_t end = xml.find("-->", i + 4);
            i = end == std::string::npos ? xml.size() : end + 3;
            continue;
        }
        if (xml.compare(i, 9, "<![CDATA[") == 0) {
            size_t end = xml.find("]]>", i + 9);
            size_t content_end = end == std::string::npos ? xml.size() : end;
            if (in_text) paragraph += xml.substr(i + 9, content_end - (i + 9));
            i = end == std::string::npos ? xml.size() : end + 3;
            continue;
        }
        if (xml.compare(i, 2, "<?") == 0) {
            size_t end = xml.find("?>", i + 2);
            i = end == std::string::npos ? xml.size() : end + 2;
            continue;
        }
        if (xml.compare(i, 2, "<!") == 0) {
            size_t end = xml.find('>', i + 2);
            i = end == std::string::npos ? xml.size() : end + 1;
            continue;
        }

        size_t end = xml.find('>', i + 1);
        if (end == std::string::npos) break;
        std::string content = trim_copy(xml.substr(i + 1, end - i - 1));
        i = end + 1;
        ++record_count;
        if (content.empty()) continue;

        bool closing = content.front() == '/';
        bool self_closing = content.back() == '/';
        if (closing) content.erase(content.begin());
        if (self_closing && !content.empty()) content.pop_back();
        content = trim_copy(content);

        size_t ws = content.find_first_of(" \t\r\n");
        std::string raw_name = ws == std::string::npos
                                   ? content
                                   : content.substr(0, ws);
        std::string name = local_tag_name(raw_name);

        if (name == "p") {
            if (closing) {
                flush();
            } else {
                flush();
                in_paragraph = true;
            }
        } else if (name == "t") {
            if (closing) {
                in_text = false;
            } else if (!self_closing) {
                in_text = true;
            }
        } else if (name == "tbl") {
            if (closing) table_depth = std::max(0, table_depth - 1);
            else if (!self_closing) ++table_depth;
        } else if ((name == "tab") && in_paragraph) {
            paragraph.push_back('\t');
        } else if ((name == "lineBreak" || name == "linebreak" ||
                    name == "br") && in_paragraph) {
            paragraph.push_back('\n');
        }
    }
    flush();
}

// HWPX ZIP을 읽어 `Contents/sectionN.xml`을 순서대로 해석한다.
DecodeResult decode_hwpx(const std::string &input_path,
                         const Options & /*options*/) {
    std::vector<uint8_t> zip = read_file(input_path);
    auto entries = read_zip_central_directory(zip);

    struct Section { int index; const ZipEntry *entry; };
    std::vector<Section> sections;
    for (const auto &entry : entries) {
        std::string normalized = entry.name;
        std::replace(normalized.begin(), normalized.end(), '\\', '/');
        const std::string prefix = "Contents/section";
        const std::string suffix = ".xml";
        if (!starts_with(normalized, prefix) || !ends_with(normalized, suffix)) {
            continue;
        }
        std::string number = normalized.substr(
            prefix.size(), normalized.size() - prefix.size() - suffix.size());
        if (!number.empty() &&
            std::all_of(number.begin(), number.end(), [](unsigned char c) {
                return std::isdigit(c) != 0;
            })) {
            sections.push_back({std::stoi(number), &entry});
        }
    }
    std::sort(sections.begin(), sections.end(),
              [](const Section &a, const Section &b) {
                  return a.index < b.index;
              });
    if (sections.empty()) {
        throw std::runtime_error("HWPX contains no Contents/sectionN.xml");
    }

    DecodeResult result;
    result.format = "hwpx";
    result.extraction_mode = "hwpx_xml_native";
    for (const auto &section : sections) {
        std::vector<uint8_t> xml_bytes = extract_zip_entry(zip, *section.entry);
        result.decompressed_bytes += xml_bytes.size();
        std::string xml(reinterpret_cast<const char *>(xml_bytes.data()),
                        xml_bytes.size());
        extract_hwpx_paragraphs(xml, result.paragraphs, result.record_count);
    }
    result.chosen_metrics = evaluate_text(paragraphs_to_text(result.paragraphs));
    result.native_metrics = result.chosen_metrics;
    return result;
}

}  // namespace body_decoder
