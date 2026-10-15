#include "model.hpp"

#include <algorithm>
#include <fstream>
#include <stdexcept>

static std::string stem(const std::string &path) {
    std::size_t slash = path.find_last_of("/\\");
    std::string base = slash == std::string::npos ? path : path.substr(slash + 1);
    std::size_t dot = base.find_last_of('.');
    return dot == std::string::npos ? base : base.substr(0, dot);
}

Instance read_taillard(const std::string &path, int n_factories, int horizon) {
    std::ifstream input(path);
    if (!input)
        throw std::runtime_error("cannot open " + path);
    Instance instance;
    if (!(input >> instance.n_jobs >> instance.n_machines) || instance.n_jobs <= 0 || instance.n_machines <= 0)
        throw std::runtime_error(path + ": bad header");
    for (int job = 0; job < instance.n_jobs; ++job) {
        for (int index = 0; index < instance.n_machines; ++index) {
            Operation operation{job * instance.n_machines + index, job, index, 0, 0};
            if (!(input >> operation.machine >> operation.duration))
                throw std::runtime_error(path + ": truncated");
            if (operation.machine < 0 || operation.machine >= instance.n_machines || operation.duration <= 0)
                throw std::runtime_error(path + ": bad operation");
            instance.operations.push_back(operation);
        }
    }
    instance.n_factories = n_factories;
    instance.horizon = horizon;
    instance.name = stem(path) + "_" + std::to_string(n_factories) + "f";
    return instance;
}

Domains build_domains(const Instance &instance) {
    Domains domains;
    std::size_t size = instance.operations.size();
    domains.earliest.assign(size, 0);
    domains.latest.assign(size, 0);
    for (int job = 0; job < instance.n_jobs; ++job) {
        int total = 0;
        for (int index = 0; index < instance.n_machines; ++index)
            total += instance.at(job, index).duration;
        int elapsed = 0;
        int remaining = total;
        for (int index = 0; index < instance.n_machines; ++index) {
            const Operation &operation = instance.at(job, index);
            domains.earliest[operation.id] = elapsed;
            domains.latest[operation.id] = instance.horizon - remaining;
            elapsed += operation.duration;
            remaining -= operation.duration;
        }
    }
    for (std::size_t id = 0; id < size; ++id)
        domains.infeasible += domains.earliest[id] > domains.latest[id];
    return domains;
}

Variables::Variables(const Instance &instance, const Domains &domains, bool symmetry)
    : symmetry(symmetry), domains_(domains), n_jobs_(instance.n_jobs), n_factories_(instance.n_factories) {
    std::size_t size = instance.operations.size();
    order_base.assign(size, 0);
    for (std::size_t id = 0; id < size; ++id) {
        order_base[id] = top + 1;
        top += std::max(0, domains.latest[id] - domains.earliest[id]);
    }
    factory_.assign(static_cast<std::size_t>(n_jobs_) * n_factories_, 0);
    used_.assign(static_cast<std::size_t>(n_jobs_) * n_factories_, 0);
    same_.assign(static_cast<std::size_t>(n_jobs_) * n_jobs_, 0);
}

int Variables::order(int operation, int time) const {
    int earliest = domains_.earliest[operation];
    if (time <= earliest)
        return kTrue;
    if (time > domains_.latest[operation])
        return kFalse;
    return order_base[operation] + time - earliest - 1;
}

int Variables::factory(int job, int factory) {
    if (symmetry) {
        if (job == 0)
            return factory == 0 ? kTrue : kFalse;
        if (factory > job)
            return kFalse;
    }
    int &slot = factory_[static_cast<std::size_t>(job) * n_factories_ + factory];
    if (!slot)
        slot = ++top;
    return slot;
}

int Variables::used(int job, int factory) {
    if (job < 0 || factory > job)
        return kFalse;
    if (job == 0)
        return factory == 0 ? kTrue : kFalse;
    int &slot = used_[static_cast<std::size_t>(job) * n_factories_ + factory];
    if (!slot)
        slot = ++top;
    return slot;
}

int Variables::same(int job_a, int job_b) {
    int &slot = same_[static_cast<std::size_t>(std::min(job_a, job_b)) * n_jobs_ + std::max(job_a, job_b)];
    if (!slot)
        slot = ++top;
    return slot;
}
