CADICAL ?= 1.9.5
CXX ?= g++
CXXFLAGS ?= -std=c++17 -O3 -DNDEBUG -Wall -Wextra

CADICAL_DIR := solver/cadical-$(CADICAL)
LIBRARY := $(CADICAL_DIR)/build/libcadical.a
BINARY := build/djsp_sat
SOURCES := $(wildcard src/*.cpp)
HEADERS := $(wildcard src/*.hpp)

.PHONY: default clean

default: $(BINARY)

$(LIBRARY):
	bash solver/fetch_cadical.sh $(CADICAL)

$(BINARY): $(SOURCES) $(HEADERS) $(LIBRARY)
	mkdir -p build
	$(CXX) $(CXXFLAGS) -I$(CADICAL_DIR)/src -o $@ $(SOURCES) $(LIBRARY)

clean:
	rm -rf build
