CXX ?= g++
CXXFLAGS ?= -O2 -std=c++17 -Wall -Wextra
CPPFLAGS := -I./hpp
LDLIBS := -lz
BUILD_DIR := build
TARGET := $(BUILD_DIR)/body_decoder
SRC := cpp/body_decoder.cpp
HEADERS := hpp/body_decoder.hpp \
	hpp/body_decoder_common.hpp \
	hpp/body_decoder_hwp.hpp \
	hpp/body_decoder_hwpx.hpp \
	hpp/body_decoder_postprocess.hpp \
	hpp/body_decoder_cli.hpp

.PHONY: all clean
all: $(TARGET)

$(TARGET): $(SRC) $(HEADERS) | $(BUILD_DIR)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -o $@ $< $(LDLIBS)

$(BUILD_DIR):
	mkdir -p $(BUILD_DIR)

clean:
	rm -f $(TARGET)
