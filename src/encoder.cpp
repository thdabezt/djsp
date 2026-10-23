#include "model.hpp"

#include <algorithm>

void Encoder::add(std::initializer_list<int> literals) {
    buffer_.clear();
    for (int literal : literals) {
        if (literal == kTrue)
            return;
        if (literal != kFalse)
            buffer_.push_back(literal);
    }
    sink_.add(buffer_);
    ++count;
}

void Encoder::add(const std::vector<int> &literals) {
    buffer_.clear();
    for (int literal : literals) {
        if (literal == kTrue)
            return;
        if (literal != kFalse)
            buffer_.push_back(literal);
    }
    sink_.add(buffer_);
    ++count;
}

void Encoder::base() {
    Variables &v = variables_;
    for (const Operation &operation : instance_.operations) {
        int o = operation.id;
        int earliest = domains_.earliest[o];
        int latest = domains_.latest[o];
        add({v.order(o, earliest)});
        add({-v.order(o, latest + 1)});
        for (int time = earliest; time <= latest; ++time)
            add({-v.order(o, time + 1), v.order(o, time)});
    }
    for (int job = 0; job < instance_.n_jobs; ++job) {
        for (int index = 0; index + 1 < instance_.n_machines; ++index) {
            const Operation &operation = instance_.at(job, index);
            const Operation &following = instance_.at(job, index + 1);
            for (int time = domains_.earliest[operation.id]; time <= domains_.latest[operation.id]; ++time)
                add({-v.order(operation.id, time), v.order(following.id, time + operation.duration)});
        }
    }
}

void Encoder::factories() {
    Variables &v = variables_;
    int n_jobs = instance_.n_jobs;
    int n_factories = instance_.n_factories;
    std::vector<int> literals;
    for (int job = 0; job < n_jobs; ++job) {
        literals.clear();
        for (int factory = 0; factory < n_factories; ++factory)
            literals.push_back(v.factory(job, factory));
        add(literals);
        for (int first = 0; first < n_factories; ++first)
            for (int second = first + 1; second < n_factories; ++second)
                add({-v.factory(job, first), -v.factory(job, second)});
    }
    if (v.symmetry) {
        for (int job = 1; job < n_jobs; ++job) {
            for (int factory = 1; factory < n_factories; ++factory)
                add({-v.factory(job, factory), v.used(job - 1, factory - 1)});
            for (int factory = 0; factory <= std::min(job, n_factories - 2); ++factory)
                add({-v.used(job, factory), v.used(job - 1, factory), v.factory(job, factory)});
        }
    }
    for (int job_a = 0; job_a < n_jobs; ++job_a) {
        for (int job_b = job_a + 1; job_b < n_jobs; ++job_b) {
            int same = v.same(job_a, job_b);
            for (int factory = 0; factory < n_factories; ++factory)
                add({-v.factory(job_a, factory), -v.factory(job_b, factory), same});
        }
    }
    for (int job_a = 0; job_a < n_jobs; ++job_a)
        for (int job_b = job_a + 1; job_b < n_jobs; ++job_b)
            for (int factory = 0; factory < n_factories; ++factory)
                add({-v.same(job_a, job_b), -v.factory(job_a, factory), v.factory(job_b, factory)});
}

static std::vector<std::vector<const Operation *>> machine_groups(const Instance &instance) {
    std::vector<std::vector<const Operation *>> groups(instance.n_machines);
    for (const Operation &operation : instance.operations)
        groups[operation.machine].push_back(&operation);
    return groups;
}

void Encoder::machines() {
    Variables &v = variables_;
    std::vector<int> clause;
    for (const auto &operations : machine_groups(instance_)) {
        for (std::size_t left_index = 0; left_index < operations.size(); ++left_index) {
            const Operation &left = *operations[left_index];
            for (std::size_t right_index = left_index + 1; right_index < operations.size(); ++right_index) {
                const Operation &right = *operations[right_index];
                if (left.job == right.job)
                    continue;
                int gate = -v.same(left.job, right.job);
                int left_first = v.fresh();
                int right_first = v.fresh();
                clause.assign({gate, left_first, right_first});
                raw(clause);
                ++count;
                struct Side {
                    int literal;
                    const Operation *before;
                    const Operation *after;
                } sides[2] = {{left_first, &left, &right}, {right_first, &right, &left}};
                for (const Side &side : sides) {
                    const Operation &before = *side.before;
                    const Operation &after = *side.after;
                    int eb = domains_.earliest[before.id], lb = domains_.latest[before.id];
                    int ea = domains_.earliest[after.id], la = domains_.latest[after.id];
                    int pb = before.duration;
                    int order_before = v.order_base[before.id] - eb - 1;
                    int order_after = v.order_base[after.id] - ea - 1;
                    int first = std::max(eb, ea - pb + 1);
                    int cut_time = la - pb + 1;
                    bool cut = cut_time <= lb;
                    int last = cut ? std::max(first, cut_time) - 1 : lb;
                    for (int time = first; time <= last; ++time) {
                        if (time <= eb)
                            clause.assign({-side.literal, order_after + time + pb});
                        else
                            clause.assign({-side.literal, -(order_before + time), order_after + time + pb});
                        raw(clause);
                    }
                    count += std::max(0, last - first + 1);
                    if (cut) {
                        int time = std::max(first, la - pb + 1);
                        if (time <= eb)
                            clause.assign({-side.literal});
                        else
                            clause.assign({-side.literal, -(order_before + time)});
                        raw(clause);
                        ++count;
                    }
                }
            }
        }
    }
}

void Encoder::makespan_bound(int bound) {
    for (int job = 0; job < instance_.n_jobs; ++job) {
        const Operation &operation = instance_.at(job, instance_.n_machines - 1);
        add({-variables_.order(operation.id, bound - operation.duration + 1)});
    }
}
