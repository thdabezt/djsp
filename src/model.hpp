#pragma once

#include <climits>
#include <string>
#include <vector>

struct Operation {
    int id;
    int job;
    int index;
    int machine;
    int duration;
};

struct Instance {
    std::string name;
    int n_jobs = 0;
    int n_machines = 0;
    int n_factories = 0;
    int horizon = 0;
    std::vector<Operation> operations;

    const Operation &at(int job, int index) const { return operations[job * n_machines + index]; }
};

struct Domains {
    std::vector<int> earliest;
    std::vector<int> latest;
    int infeasible = 0;
};

Instance read_taillard(const std::string &path, int n_factories, int horizon);
Domains build_domains(const Instance &instance);

constexpr int kTrue = INT_MAX;
constexpr int kFalse = -INT_MAX;

class ClauseSink {
  public:
    virtual ~ClauseSink() = default;
    virtual void add(const std::vector<int> &clause) = 0;
};

class Variables {
  public:
    Variables(const Instance &instance, const Domains &domains, bool symmetry);

    int order(int operation, int time) const;
    int factory(int job, int factory);
    int used(int job, int factory);
    int same(int job_a, int job_b);
    int fresh() { return ++top; }

    int top = 0;
    bool symmetry;
    std::vector<int> order_base;

  private:
    const Domains &domains_;
    int n_jobs_;
    int n_factories_;
    std::vector<int> factory_;
    std::vector<int> used_;
    std::vector<int> same_;
};

class Encoder {
  public:
    Encoder(const Instance &instance, const Domains &domains, Variables &variables, ClauseSink &sink)
        : instance_(instance), domains_(domains), variables_(variables), sink_(sink) {}

    void base();
    void factories();
    void machines();
    void makespan_bound(int bound);

    long long count = 0;

  private:
    void add(std::initializer_list<int> literals);
    void add(const std::vector<int> &literals);
    void raw(const std::vector<int> &clause) { sink_.add(clause); }

    const Instance &instance_;
    const Domains &domains_;
    Variables &variables_;
    ClauseSink &sink_;
    std::vector<int> buffer_;
};
