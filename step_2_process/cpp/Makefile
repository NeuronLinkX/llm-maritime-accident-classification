CXX ?= g++
CXXFLAGS ?= -O3 -std=c++17 -Wall -Wextra

benchmark: benchmark.cpp
	$(CXX) $(CXXFLAGS) -o benchmark benchmark.cpp

clean:
	rm -f benchmark

.PHONY: clean
