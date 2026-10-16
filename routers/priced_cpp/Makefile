# clang++ when installed, else g++; `make CXX=...` still overrides
ifeq ($(origin CXX),default)
CXX := $(shell command -v clang++ >/dev/null 2>&1 && echo clang++ || echo g++)
endif
CXXFLAGS = -O3 -std=c++17 -Wall -Wextra -Wpedantic

.PHONY: all test
all: build/router build/optimizer

build/router: router.cpp
	mkdir -p build
	$(CXX) $(CXXFLAGS) router.cpp -o $@

test: build/router build/optimizer
	python3 -m unittest discover -s tests -v

build/optimizer: optimize.cpp router.cpp
	mkdir -p build
	$(CXX) $(CXXFLAGS) optimize.cpp -o $@
