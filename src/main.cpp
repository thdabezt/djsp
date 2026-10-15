#include "model.hpp"

#include <cadical.hpp>

#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <map>
#include <sstream>
#include <stdexcept>

using Clock = std::chrono::steady_clock;

namespace {

const Clock::time_point kStarted = Clock::now();

double elapsed() { return std::chrono::duration<double>(Clock::now() - kStarted).count(); }

class SolverSink : public ClauseSink {
  public:
    explicit SolverSink(CaDiCaL::Solver &solver) : solver_(solver) {}
    void add(const std::vector<int> &clause) override {
        for (int literal : clause)
            solver_.add(literal);
        solver_.add(0);
    }

  private:
    CaDiCaL::Solver &solver_;
};

class DimacsSink : public ClauseSink {
  public:
    explicit DimacsSink(std::ostream &output) : output_(output) {}
    void add(const std::vector<int> &clause) override {
        for (int literal : clause)
            output_ << literal << ' ';
        output_ << "0\n";
    }

  private:
    std::ostream &output_;
};

class Deadline : public CaDiCaL::Terminator {
  public:
    explicit Deadline(double seconds) : end_(kStarted + std::chrono::duration_cast<Clock::duration>(
                                                           std::chrono::duration<double>(seconds))) {}
    bool terminate() override { return Clock::now() >= end_; }

  private:
    Clock::time_point end_;
};

struct Options {
    std::string instance;
    int factories = 0;
    int horizon = 0;
    double time_limit = 3600.0;
    bool symmetry = true;
    std::string out;
    std::string dump;
    int bound = -1;
};

Options parse(int argc, char **argv) {
    std::map<std::string, std::string> values;
    for (int i = 1; i < argc; ++i) {
        std::string key = argv[i];
        if (key.rfind("--", 0) != 0 || i + 1 >= argc)
            throw std::runtime_error("bad argument " + key);
        values[key.substr(2)] = argv[++i];
    }
    Options options;
    for (const auto &[key, value] : values) {
        if (key == "instance")
            options.instance = value;
        else if (key == "factories")
            options.factories = std::stoi(value);
        else if (key == "horizon")
            options.horizon = std::stoi(value);
        else if (key == "time-limit")
            options.time_limit = std::stod(value);
        else if (key == "symmetry") {
            if (value != "on" && value != "off")
                throw std::runtime_error("--symmetry must be on or off");
            options.symmetry = value == "on";
        } else if (key == "out")
            options.out = value;
        else if (key == "dump")
            options.dump = value;
        else if (key == "bound")
            options.bound = std::stoi(value);
        else
            throw std::runtime_error("unknown option --" + key);
    }
    if (options.instance.empty() || options.factories <= 0 || options.horizon <= 0)
        throw std::runtime_error("--instance, --factories and --horizon are required");
    if (options.out.empty() && options.dump.empty())
        throw std::runtime_error("--out or --dump is required");
    return options;
}

void encode(Encoder &encoder) {
    encoder.base();
    encoder.factories();
    encoder.machines();
}

struct Iteration {
    int bound;
    std::string status;
    int makespan;
    double iteration_time;
    double elapsed;
};

struct State {
    std::string status = "RUNNING";
    int makespan = -1;
    long long vars = 0;
    long long clauses = 0;
    double build_time = 0.0;
    double first = -1.0, latest = -1.0, unsat = -1.0;
    std::vector<Iteration> iterations;
    std::vector<std::vector<int>> starts;
    std::vector<int> factories;
};

std::string number(double value) {
    if (value < 0)
        return "\"\"";
    std::ostringstream text;
    text.precision(6);
    text << std::fixed << value;
    return text.str();
}

void write(const std::string &path, const State &state) {
    std::ostringstream json;
    json << "{\n  \"status\": \"" << state.status << "\",\n";
    json << "  \"solver\": \"CaDiCaL " << CaDiCaL::Solver::version() << "\",\n";
    json << "  \"makespan\": " << (state.makespan < 0 ? "null" : std::to_string(state.makespan)) << ",\n";
    json << "  \"vars\": " << state.vars << ",\n  \"clauses\": " << state.clauses << ",\n";
    json << "  \"build_time\": " << number(state.build_time) << ",\n";
    json << "  \"solve_time\": " << number(elapsed()) << ",\n";
    json << "  \"time_first_sat\": " << number(state.first) << ",\n";
    json << "  \"time_latest_sat\": " << number(state.latest) << ",\n";
    json << "  \"time_unsat\": " << number(state.unsat) << ",\n";
    json << "  \"iterations\": [";
    for (std::size_t i = 0; i < state.iterations.size(); ++i) {
        const Iteration &it = state.iterations[i];
        json << (i ? ",\n    " : "\n    ") << "{\"bound\": " << it.bound << ", \"status\": \"" << it.status << "\"";
        if (it.makespan >= 0)
            json << ", \"makespan\": " << it.makespan;
        json << ", \"iteration_time\": " << number(it.iteration_time) << ", \"elapsed\": " << number(it.elapsed)
             << "}";
    }
    json << (state.iterations.empty() ? "],\n" : "\n  ],\n");
    json << "  \"starts\": ";
    if (state.starts.empty()) {
        json << "null";
    } else {
        json << "[";
        for (std::size_t job = 0; job < state.starts.size(); ++job) {
            json << (job ? ", [" : "[");
            for (std::size_t i = 0; i < state.starts[job].size(); ++i)
                json << (i ? ", " : "") << state.starts[job][i];
            json << "]";
        }
        json << "]";
    }
    json << ",\n  \"factories\": ";
    if (state.factories.empty()) {
        json << "null";
    } else {
        json << "[";
        for (std::size_t job = 0; job < state.factories.size(); ++job)
            json << (job ? ", " : "") << state.factories[job];
        json << "]";
    }
    json << "\n}\n";
    std::string temporary = path + ".tmp";
    {
        std::ofstream output(temporary);
        output << json.str();
    }
    if (std::rename(temporary.c_str(), path.c_str()) != 0)
        throw std::runtime_error("cannot write " + path);
}

int dump(const Options &options, const Instance &instance, const Domains &domains) {
    Variables variables(instance, domains, options.symmetry);
    int bound = options.bound < 0 ? instance.horizon : options.bound;
    std::string body = options.dump + ".body";
    long long count = 0;
    {
        std::ofstream output(body);
        DimacsSink sink(output);
        Encoder encoder(instance, domains, variables, sink);
        encode(encoder);
        encoder.makespan_bound(bound);
        count = encoder.count;
    }
    {
        std::ofstream output(options.dump);
        output << "c " << instance.name << " symmetry=" << (options.symmetry ? "on" : "off")
               << " makespan<=" << bound << " horizon=" << instance.horizon << "\n";
        output << "p cnf " << variables.top << " " << count << "\n";
        std::ifstream input(body);
        output << input.rdbuf();
    }
    std::remove(body.c_str());
    std::cout << options.dump << std::endl;
    return 0;
}

int solve(const Options &options, const Instance &instance, const Domains &domains) {
    State state;
    if (domains.infeasible) {
        state.status = "FAILED";
        write(options.out, state);
        return 0;
    }
    CaDiCaL::Solver solver;
    Variables variables(instance, domains, options.symmetry);
    SolverSink sink(solver);
    Encoder encoder(instance, domains, variables, sink);
    encode(encoder);
    state.build_time = elapsed();
    state.vars = variables.top;
    state.clauses = encoder.count;
    std::cout << "[" << instance.name << "] horizon=" << instance.horizon << " vars=" << state.vars
              << " clauses=" << state.clauses << " build=" << state.build_time << "s" << std::endl;

    Deadline deadline(options.time_limit);
    solver.connect_terminator(&deadline);
    int bound = instance.horizon;
    state.status = "TIMEOUT";
    while (bound >= 0) {
        encoder.makespan_bound(bound);
        state.clauses = encoder.count;
        double started = elapsed();
        int result = deadline.terminate() ? 0 : solver.solve();
        double iteration_time = elapsed() - started;
        if (result == 0) {
            state.iterations.push_back({bound, "TIMEOUT", -1, iteration_time, elapsed()});
            state.status = state.makespan >= 0 ? "FEASIBLE" : "TIMEOUT";
            std::cout << "[" << instance.name << "] time limit at Cmax<=" << bound << std::endl;
            break;
        }
        if (result == 20) {
            state.unsat = iteration_time;
            state.iterations.push_back({bound, "UNSAT", -1, iteration_time, elapsed()});
            state.status = state.makespan >= 0 ? "OPTIMAL" : "UNSAT";
            std::cout << "[" << instance.name << "] UNSAT Cmax<=" << bound << " proof_time=" << iteration_time
                      << "s" << std::endl;
            break;
        }
        std::vector<std::vector<int>> starts(instance.n_jobs);
        std::vector<int> factories(instance.n_jobs, -1);
        int makespan = 0;
        for (int job = 0; job < instance.n_jobs; ++job) {
            for (int index = 0; index < instance.n_machines; ++index) {
                const Operation &operation = instance.at(job, index);
                int time = domains.earliest[operation.id];
                while (time < domains.latest[operation.id] && solver.val(variables.order(operation.id, time + 1)) > 0)
                    ++time;
                starts[job].push_back(time);
            }
            makespan = std::max(makespan, starts[job].back() + instance.at(job, instance.n_machines - 1).duration);
            int selected = 0;
            for (int factory = 0; factory < instance.n_factories; ++factory) {
                int literal = variables.factory(job, factory);
                if (literal == kTrue || (literal != kFalse && solver.val(literal) > 0)) {
                    factories[job] = factory;
                    ++selected;
                }
            }
            if (selected != 1)
                throw std::runtime_error("model selected " + std::to_string(selected) + " factories for job " +
                                         std::to_string(job));
        }
        if (makespan > bound)
            throw std::runtime_error("model makespan " + std::to_string(makespan) + " exceeds bound " +
                                     std::to_string(bound));
        state.makespan = makespan;
        state.starts = starts;
        state.factories = factories;
        if (state.first < 0)
            state.first = elapsed();
        state.latest = elapsed();
        state.iterations.push_back({bound, "SAT", makespan, iteration_time, elapsed()});
        std::cout << "[" << instance.name << "] SAT Cmax<=" << bound << " makespan=" << makespan
                  << " iteration_time=" << iteration_time << "s" << std::endl;
        state.status = "RUNNING";
        write(options.out, state);
        state.status = "TIMEOUT";
        bound = makespan - 1;
    }
    write(options.out, state);
    return 0;
}

}

int main(int argc, char **argv) {
    try {
        Options options = parse(argc, argv);
        Instance instance = read_taillard(options.instance, options.factories, options.horizon);
        Domains domains = build_domains(instance);
        return options.dump.empty() ? solve(options, instance, domains) : dump(options, instance, domains);
    } catch (const std::bad_alloc &) {
        std::cerr << "out of memory (std::bad_alloc)" << std::endl;
        return 3;
    } catch (const std::exception &error) {
        std::cerr << "error: " << error.what() << std::endl;
        return 2;
    }
}
