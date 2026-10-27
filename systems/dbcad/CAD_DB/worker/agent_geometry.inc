// ACIS-backed agent observations and feature operations. Included in session.cpp
// so native object ownership and geometry remain inside the existing worker.
namespace {

constexpr double kAgentPi = 3.14159265358979323846;

void agentCheck(const outcome& result, const char* operation) {
    if (!result.ok())
        throw CommandError(std::string(operation) + ": " + find_err_mess(result.error_number()));
}

struct AgentEntity {
    ENTITY* value = nullptr;
    explicit AgentEntity(ENTITY* entity = nullptr) : value(entity) {}
    ~AgentEntity() { if (value) api_del_entity(value); }
    AgentEntity(const AgentEntity&) = delete;
    AgentEntity& operator=(const AgentEntity&) = delete;
    ENTITY* release() { ENTITY* result = value; value = nullptr; return result; }
};

ENTITY* agentCopy(ENTITY* entity) {
    ENTITY* copied = nullptr;
    agentCheck(api_deep_down_copy_entity(entity, copied), "copy geometry");
    if (!copied) throw CommandError("copy geometry returned no entity");
    return copied;
}

double agentNumber(const json& object, const char* key, double fallback = 0) {
    const double value = scalar(object, key, fallback);
    if (!std::isfinite(value) || std::abs(value) > 1e6)
        throw CommandError(std::string(key) + " must be a finite value within 1e6");
    return value;
}

double agentPositive(const json& object, const char* key) {
    const double value = agentNumber(object, key);
    if (value <= 1e-9) throw CommandError(std::string(key) + " must be positive");
    return value;
}

SPAposition agentPoint(const json& value, const char* name) {
    SPAposition point = vec3(value, name);
    if (!std::isfinite(point.x()) || !std::isfinite(point.y()) || !std::isfinite(point.z()) ||
        std::max({std::abs(point.x()), std::abs(point.y()), std::abs(point.z())}) > 1e6)
        throw CommandError(std::string(name) + " contains an invalid coordinate");
    return point;
}

json agentXYZ(const SPAposition& point) {
    return json::array({point.x(), point.y(), point.z()});
}

json agentXYZ(const SPAvector& vector) {
    return json::array({vector.x(), vector.y(), vector.z()});
}

json agentBounds(ENTITY* entity) {
    SPAposition low, high;
    agentCheck(api_get_entity_box(entity, low, high), "bounding box");
    return {{"min", agentXYZ(low)}, {"max", agentXYZ(high)}};
}

SPAposition agentBoxCenter(const json& box) {
    return SPAposition((box["min"][0].get<double>() + box["max"][0].get<double>()) / 2,
                       (box["min"][1].get<double>() + box["max"][1].get<double>()) / 2,
                       (box["min"][2].get<double>() + box["max"][2].get<double>()) / 2);
}

std::vector<ENTITY*> agentTopology(ENTITY* entity, bool faces) {
    ENTITY_LIST native;
    agentCheck(faces ? api_get_faces(entity, native) : api_get_edges(entity, native),
               faces ? "list faces" : "list edges");
    std::vector<ENTITY*> result;
    for (ENTITY* item = native.first(); item; item = native.next()) result.push_back(item);
    return result;
}

std::vector<int> agentIndices(const json& params, const char* name, size_t count, bool allowEmpty = false) {
    if (!params.contains(name) || !params[name].is_array()) {
        if (allowEmpty) return {};
        throw CommandError(std::string(name) + " must be an index array");
    }
    if (params[name].size() > 4096) throw CommandError("too many topology selections");
    std::set<int> unique;
    for (const json& value : params[name]) {
        if (!value.is_number_integer()) throw CommandError("topology indices must be integers");
        const int index = value.get<int>();
        if (index < 0 || static_cast<size_t>(index) >= count)
            throw CommandError("topology index is outside the current component");
        unique.insert(index);
    }
    if (unique.empty() && !allowEmpty) throw CommandError("topology selection is empty");
    return {unique.begin(), unique.end()};
}

struct AgentMass {
    double volume = 0;
    double relativeAccuracy = 0;
    SPAposition centroid{0, 0, 0};
};

AgentMass agentMass(ENTITY* entity) {
    AgentMass result;
    if (!is_BODY(entity)) return result;
    // All callers use owned temporary copies, so derived mass-property caches
    // cannot change the authoritative session or its SAT bytes.
    mass_props values;
    mass_props_options options;
    options.set_level(VOLUME_AND_CENTROID);
    options.set_req_rel_accy(1e-8);
    agentCheck(api_body_mass_props(static_cast<BODY*>(entity), values, &options), "mass properties");
    result.volume = values.get_volume();
    result.relativeAccuracy = values.get_rel_accy_vol_achieved();
    result.centroid = values.get_centroid();
    if (!std::isfinite(result.volume)) throw CommandError("non-finite mass properties");
    return result;
}

json agentSummary(ENTITY* entity, const std::string& id) {
    const auto faces = agentTopology(entity, true);
    const auto edges = agentTopology(entity, false);
    const json box = agentBounds(entity);
    insanity_list* problems = nullptr;
    agentCheck(api_check_entity(entity, problems), "BRep checker");
    const int errors = problems ? problems->count() : 0;
    delete problems;
    double area = 0, accuracy = 0;
    if (!faces.empty()) agentCheck(api_ent_area(entity, 1e-7, area, accuracy), "surface area");
    const AgentMass mass = agentMass(entity);
    int solids = 0;
    if (is_BODY(entity) && mass.volume > 1e-12) {
        bool closed = true;
        for (ENTITY* item : faces) if (static_cast<FACE*>(item)->sides()) closed = false;
        if (closed)
            for (LUMP* lump = static_cast<BODY*>(entity)->lump(); lump; lump = lump->next()) ++solids;
    }
    return {{"id", id}, {"bbox", box},
            {"size", json::array({box["max"][0].get<double>() - box["min"][0].get<double>(),
                                  box["max"][1].get<double>() - box["min"][1].get<double>(),
                                  box["max"][2].get<double>() - box["min"][2].get<double>()})},
            {"volume", mass.volume}, {"volume_relative_accuracy", mass.relativeAccuracy},
            {"centroid", agentXYZ(mass.centroid)}, {"area", area},
            {"area_relative_accuracy", accuracy}, {"valid", errors == 0}, {"checker_problems", errors},
            {"solids", solids}, {"faces", faces.size()}, {"edges", edges.size()}};
}

json agentFace(FACE* face, int index) {
    const json box = agentBounds(face);
    json result = {{"index", index}, {"type", "UNKNOWN"}, {"bbox", box},
                   {"center", agentXYZ(agentBoxCenter(box))}, {"center_kind", "bbox_center"}};
    double area = 0, accuracy = 0;
    agentCheck(api_ent_area(face, 1e-7, area, accuracy), "face area");
    result["area"] = area;
    result["area_relative_accuracy"] = accuracy;
    if (!face->geometry()) return result;
    const surface& equation = face->geometry()->equation();
    const std::string type = equation.type_name();
    result["type"] = type;
    if (type == "plane") {
        double momentA = 0, momentB = 0;
        SPAposition center;
        SPAunit_vector axisA, axisB;
        agentCheck(api_planar_face_pr(face, 1e-7, area, center, momentA, momentB, axisA, axisB, accuracy),
                   "planar face centroid");
        const SPAtransf transform = get_owner_transf(face);
        center *= transform;
        const plane& planar = static_cast<const plane&>(equation);
        SPAvector normal(planar.normal.x(), planar.normal.y(), planar.normal.z());
        if (face->sense() == REVERSED) normal = -normal;
        normal *= transform;
        if (normal.len() > 1e-12) normal = normal / normal.len();
        result["center"] = agentXYZ(center);
        result["center_kind"] = "planar_area_centroid";
        result["normal"] = agentXYZ(normal);
    } else if (type == "cone") {
        const cone& conical = static_cast<const cone&>(equation);
        if (std::abs(conical.sine_angle) < 1e-12) result["type"] = "cylinder";
    }
    return result;
}

json agentEdge(EDGE* edge, int index) {
    const json box = agentBounds(edge);
    json result = {{"index", index}, {"type", "UNKNOWN"}, {"bbox", box},
                   {"center", agentXYZ(agentBoxCenter(box))}, {"center_kind", "bbox_center"},
                   {"length", edge->length(FALSE, TRUE)}};
    if (!edge->geometry()) return result;
    const curve& equation = edge->geometry()->equation();
    const std::string type = equation.type_name();
    result["type"] = type;
    if (type == "ellipse") {
        const ellipse& elliptical = static_cast<const ellipse&>(equation);
        if (std::abs(elliptical.radius_ratio - 1.0) < 1e-9) {
            result["type"] = "circle";
            SPAvector majorAxis = elliptical.major_axis;
            majorAxis *= get_owner_transf(edge);
            result["radius"] = majorAxis.len();
        }
    }
    return result;
}

struct AgentFrame {
    SPAposition origin{0, 0, 0};
    SPAvector u{1, 0, 0}, v{0, 1, 0}, normal{0, 0, 1};
    SPAposition point(double a, double b) const { return origin + u * a + v * b; }
};

AgentFrame agentFrame(const json& params) {
    AgentFrame frame;
    if (params.contains("origin")) frame.origin = agentPoint(params["origin"], "origin");
    const std::string planeName = params.value("plane", "XY");
    if (planeName == "XZ") { frame.u = SPAvector(1, 0, 0); frame.v = SPAvector(0, 0, 1); frame.normal = SPAvector(0, -1, 0); }
    else if (planeName == "YZ") { frame.u = SPAvector(0, 1, 0); frame.v = SPAvector(0, 0, 1); frame.normal = SPAvector(1, 0, 0); }
    else if (planeName != "XY") throw CommandError("plane must be XY, XZ or YZ");
    return frame;
}

ENTITY* agentProfile(const json& profile, const AgentFrame& frame) {
    const std::string kind = profile.value("kind", "");
    FACE* face = nullptr;
    if (kind == "circle") {
        agentCheck(api_make_planar_disk(frame.origin, SPAunit_vector(frame.normal.x(), frame.normal.y(), frame.normal.z()), agentPositive(profile, "radius"), face),
                   "circle profile");
        return face;
    }
    if (kind == "rectangle") {
        const double width = agentPositive(profile, "width"), height = agentPositive(profile, "height");
        // api_make_plface normal = (right-origin) cross (left-origin).
        agentCheck(api_make_plface(frame.point(-width / 2, -height / 2), frame.point(-width / 2, height / 2),
                                  frame.point(width / 2, -height / 2), face), "rectangle profile");
        return face;
    }
    if (kind != "polygon" || !profile.contains("points") || !profile["points"].is_array() ||
        profile["points"].size() < 3 || profile["points"].size() > 1024)
        throw CommandError("profile requires circle, rectangle or polygon with at least three points");
    std::vector<SPAposition> points;
    for (const json& point : profile["points"]) {
        if (!point.is_array() || point.size() != 2 || !point[0].is_number() || !point[1].is_number())
            throw CommandError("polygon points must be [u,v] pairs");
        const double u = point[0].get<double>(), v = point[1].get<double>();
        if (!std::isfinite(u) || !std::isfinite(v) || std::abs(u) > 1e6 || std::abs(v) > 1e6)
            throw CommandError("invalid polygon coordinate");
        points.push_back(frame.point(u, v));
    }
    if ((points.front() - points.back()).len() < 1e-9) points.pop_back();
    if (points.size() < 3) throw CommandError("polygon needs three distinct vertices");
    std::vector<EDGE*> edges;
    try {
        for (size_t i = 0; i < points.size(); ++i) {
            EDGE* edge = nullptr;
            agentCheck(api_curve_line(points[i], points[(i + 1) % points.size()], edge), "polygon edge");
            edges.push_back(edge);
        }
        BODY* wire = nullptr;
        agentCheck(api_make_ewire(static_cast<int>(edges.size()), edges.data(), wire), "polygon wire");
        edges.clear();
        AgentEntity owner(wire);
        agentCheck(api_cover_wires(wire, static_cast<cover_options*>(nullptr)), "polygon face");
        return owner.release();
    } catch (...) {
        for (EDGE* edge : edges) api_del_entity(edge);
        throw;
    }
}

bool agentOwnedBy(ENTITY* entity, ENTITY* root) {
    for (int i = 0; entity && i < 16; ++i, entity = entity->owner()) if (entity == root) return true;
    return false;
}

BODY* agentExtrudeOrRevolve(const std::string& action, const json& params) {
    if (!params.contains("profile") || !params["profile"].is_object()) throw CommandError("profile object required");
    const AgentFrame frame = agentFrame(params);
    AgentEntity profile(agentProfile(params["profile"], frame));
    BODY* output = nullptr;
    sweep_options options;
    options.set_solid(TRUE);
    if (action == "component.extrude") {
        const double distance = agentNumber(params, "distance");
        if (std::abs(distance) < 1e-9) throw CommandError("extrusion distance cannot be zero");
        agentCheck(api_sweep_with_options(profile.value, frame.normal * distance, &options, output), "extrude profile");
    } else {
        const SPAposition axisOrigin = params.contains("axis_origin") ? agentPoint(params["axis_origin"], "axis_origin") : SPAposition(0, 0, 0);
        const SPAposition axisPoint = params.contains("axis") ? agentPoint(params["axis"], "axis") : SPAposition(0, 0, 1);
        const SPAvector axis(axisPoint.x(), axisPoint.y(), axisPoint.z());
        if (axis.len() < 1e-9) throw CommandError("revolution axis cannot be zero");
        const double degrees = agentNumber(params, "angle_degrees", 360);
        if (std::abs(degrees) < 1e-9 || std::abs(degrees) > 360) throw CommandError("revolution angle must be nonzero and within 360 degrees");
        options.set_sweep_angle(degrees * kAgentPi / 180);
        agentCheck(api_sweep_with_options(profile.value, axisOrigin, axis, &options, output), "revolve profile");
    }
    // Sweeping a sheet BODY modifies that body and deliberately returns a null
    // output pointer; standalone FACE profiles instead return a new BODY.
    if (!output && is_BODY(profile.value)) output = static_cast<BODY*>(profile.release());
    if (!output) throw CommandError("sweep returned no body");
    if (agentOwnedBy(profile.value, output)) profile.release();
    return output;
}

BODY* agentSweep(const json& params) {
    const double radius = agentPositive(params, "radius");
    if (!params.contains("path") || !params["path"].is_object()) throw CommandError("path object required");
    const json& path = params["path"];
    const std::string kind = path.value("kind", "");
    std::vector<SPAposition> points;
    if (path.contains("points")) {
        if (!path["points"].is_array() || path["points"].size() > 1024) throw CommandError("invalid path points");
        for (const json& point : path["points"]) points.push_back(agentPoint(point, "path point"));
    }
    AgentEntity pathOwner;
    SPAposition start;
    SPAvector tangent;
    if (kind == "polyline") {
        if (points.size() < 2) throw CommandError("polyline needs at least two points");
        start = points[0]; tangent = points[1] - points[0];
        std::vector<EDGE*> edges;
        try {
            for (size_t i = 1; i < points.size(); ++i) {
                EDGE* edge = nullptr;
                agentCheck(api_curve_line(points[i - 1], points[i], edge), "path segment");
                edges.push_back(edge);
            }
            BODY* wire = nullptr;
            agentCheck(api_make_ewire(static_cast<int>(edges.size()), edges.data(), wire), "path wire");
            edges.clear(); pathOwner.value = wire;
        } catch (...) { for (EDGE* edge : edges) api_del_entity(edge); throw; }
    } else if (kind == "arc") {
        EDGE* arc = nullptr;
        if (points.size() == 3) {
            start = points[0];
            agentCheck(api_curve_arc_3pt(points[0], points[1], points[2], FALSE, arc), "arc path");
            const SPAvector a = points[1] - points[0], b = points[2] - points[1];
            const SPAvector normal = a * b;
            const curve& equation = arc->geometry()->equation();
            if (std::string(equation.type_name()) != "ellipse") { api_del_entity(arc); throw CommandError("arc geometry is not circular"); }
            const ellipse& circle = static_cast<const ellipse&>(equation);
            tangent = normal * (start - circle.centre);
        } else {
            const SPAposition center = agentPoint(path.at("center"), "arc center");
            const SPAposition n = path.contains("normal") ? agentPoint(path["normal"], "normal") : SPAposition(0, 0, 1);
            SPAvector normal(n.x(), n.y(), n.z());
            if (normal.len() < 1e-9) throw CommandError("arc normal cannot be zero");
            normal = normal / normal.len();
            const double bendRadius = agentPositive(path, "radius");
            const double startAngle = agentNumber(path, "start_angle") * kAgentPi / 180;
            const double angle = agentNumber(path, "sweep_angle", 90) * kAgentPi / 180;
            if (std::abs(angle) < 1e-9 || std::abs(angle) >= 2 * kAgentPi) throw CommandError("arc sweep must be nonzero and less than 360 degrees");
            SPAvector u = std::abs(normal.z()) > 0.9 ? SPAvector(1, 0, 0) : SPAvector(0, 0, 1) * normal;
            u = u / u.len(); const SPAvector v = normal * u;
            const auto point = [&](double a) { return center + u * (bendRadius * std::cos(a)) + v * (bendRadius * std::sin(a)); };
            start = point(startAngle);
            tangent = (u * (-std::sin(startAngle)) + v * std::cos(startAngle)) * (angle > 0 ? 1 : -1);
            agentCheck(api_curve_arc_3pt(start, point(startAngle + angle / 2), point(startAngle + angle), FALSE, arc), "arc path");
        }
        pathOwner.value = arc;
    } else throw CommandError("path kind must be polyline or arc");
    if (tangent.len() < 1e-9) throw CommandError("path tangent is zero");
    FACE* disk = nullptr;
    agentCheck(api_make_planar_disk(start, SPAunit_vector(tangent.x(), tangent.y(), tangent.z()), radius, disk), "sweep profile");
    AgentEntity profile(disk);
    sweep_options options; options.set_solid(TRUE);
    BODY* output = nullptr;
    agentCheck(api_sweep_with_options(profile.value, pathOwner.value, &options, output), "sweep along path");
    if (!output) throw CommandError("path sweep returned no body");
    if (agentOwnedBy(profile.value, output)) profile.release();
    if (agentOwnedBy(pathOwner.value, output)) pathOwner.release();
    return output;
}

} // namespace

std::string GeometrySession::query(const std::string& queryJson) {
    const json request = json::parse(queryJson);
    const std::string kind = request.value("kind", "inspect");
    const std::string component = request.value("component", "");
    const auto required = [&](const std::string& id) -> ComponentRef* {
        ComponentRef* found = find(id);
        if (!found) throw CommandError("unknown component sid: " + id);
        return found;
    };
    if (kind == "inspect" || kind == "validate") {
        json items = json::array(); int solids = 0; double volume = 0; bool valid = true;
        if (!component.empty()) required(component);
        for (const ComponentRef& item : components_) {
            if (!component.empty() && item.sid != component) continue;
            AgentEntity copy(agentCopy(item.entity));
            json summary = agentSummary(copy.value, item.sid);
            solids += summary["solids"].get<int>(); volume += summary["volume"].get<double>();
            valid = valid && summary["valid"].get<bool>(); items.push_back(summary);
        }
        return json{{"kind", kind}, {"components", items}, {"valid", valid}, {"solids", solids},
                    {"volume", volume}, {"units", "mm"}}.dump();
    }
    if (kind == "distance" || kind == "interference") {
        ComponentRef* a = required(request.value("a", ""));
        ComponentRef* b = required(request.value("b", ""));
        AgentEntity first(agentCopy(a->entity)), second(agentCopy(b->entity));
        SPAposition pa, pb; double distance = 0;
        agentCheck(api_entity_entity_distance(first.value, second.value, pa, pb, distance), "entity distance");
        json result = {{"kind", kind}, {"a", a->sid}, {"b", b->sid}, {"distance", distance},
                       {"point_a", agentXYZ(pa)}, {"point_b", agentXYZ(pb)}, {"units", "mm"}};
        if (kind == "interference") {
            if (!is_BODY(first.value) || !is_BODY(second.value)) throw CommandError("interference requires solid components");
            BODY* intersection = nullptr;
            agentCheck(api_boolean(static_cast<BODY*>(second.value), static_cast<BODY*>(first.value), INTERSECTION,
                                   NDBOOL_KEEP_BOTH, intersection), "intersection volume");
            AgentEntity output(intersection);
            const double volume = intersection && intersection->lump() ? agentMass(intersection).volume : 0;
            result["intersection_volume"] = volume;
            result["interferes"] = volume > 1e-9;
        }
        return result.dump();
    }
    if (kind != "faces" && kind != "edges" && kind != "render_selection")
        throw CommandError("unsupported geometry query kind");
    ComponentRef* source = required(component);
    AgentEntity copy(agentCopy(source->entity));
    if (kind == "render_selection") {
        json selections = json::array();
        for (const bool faces : {true, false}) {
            const char* name = faces ? "faces" : "edges";
            const auto topology = agentTopology(copy.value, faces);
            for (int index : agentIndices(request, name, topology.size(), true)) {
                MeshResult mesh = MeshExtractor::extractEntity(*topology[index], MeshQuality::MediumFine);
                if (!mesh.ok) throw CommandError("selection mesh failed: " + mesh.error);
                json runs = json::array();
                for (const auto& run : mesh.mesh.edges)
                    runs.push_back({{"firstVertex", run.firstVertex}, {"vertexCount", run.vertexCount}});
                selections.push_back({{"kind", faces ? "face" : "edge"}, {"index", index},
                                      {"positions", mesh.mesh.positions}, {"indices", mesh.mesh.indices},
                                      {"edge_positions", mesh.mesh.edgePositions}, {"edge_runs", runs}});
            }
        }
        if (selections.empty()) throw CommandError("selection is empty");
        return json{{"kind", kind}, {"component", component}, {"selections", selections}, {"units", "mm"}}.dump();
    }
    const bool faces = kind == "faces";
    const auto topology = agentTopology(copy.value, faces);
    const int limit = request.value("limit", 50), offset = request.value("offset", 0);
    if (limit < 1 || limit > 1000 || offset < 0) throw CommandError("query limit must be 1..1000 and offset nonnegative");
    json items = json::array();
    for (size_t i = static_cast<size_t>(offset); i < topology.size() && items.size() < static_cast<size_t>(limit); ++i)
        items.push_back(faces ? agentFace(static_cast<FACE*>(topology[i]), static_cast<int>(i))
                              : agentEdge(static_cast<EDGE*>(topology[i]), static_cast<int>(i)));
    return json{{"kind", kind}, {"component", component}, {"count", topology.size()}, {"items", items},
                {"offset", offset}, {"units", "mm"}}.dump();
}

std::string GeometrySession::execAdvanced(const std::string& commandJson) {
    const json command = json::parse(commandJson);
    const std::string action = command.at("action").get<std::string>();
    const json& params = command.at("params");
    json result = {{"added", json::array()}, {"modified", json::array()}, {"removed", json::array()}};
    if (action == "component.extrude" || action == "component.revolve" || action == "component.sweep") {
        AgentEntity output(action == "component.sweep" ? agentSweep(params) : agentExtrudeOrRevolve(action, params));
        const json checked = agentSummary(output.value, "new");
        if (!checked["valid"].get<bool>() || checked["solids"].get<int>() < 1)
            throw CommandError("feature did not produce valid solid geometry");
        ComponentRef created{"n" + std::to_string(nextNewSid_), output.value, action.substr(10)};
        result["added"].push_back(json::parse(emitComponent(created, true)));
        components_.push_back(created); output.release(); ++nextNewSid_;
        return result.dump();
    }
    ComponentRef* target = find(params.value("component", ""));
    if (!target || !is_BODY(target->entity)) throw CommandError("feature requires an existing body component");
    AgentEntity copy(agentCopy(target->entity));
    if (action == "component.shell") {
        const auto topology = agentTopology(copy.value, true);
        std::vector<FACE*> openings;
        for (int index : agentIndices(params, "faces", topology.size(), true)) openings.push_back(static_cast<FACE*>(topology[index]));
        const double thickness = agentNumber(params, "thickness");
        if (std::abs(thickness) < 1e-9) throw CommandError("shell thickness cannot be zero");
        agentCheck(api_hollow_body(static_cast<BODY*>(copy.value), static_cast<int>(openings.size()), openings.data(), thickness), "shell body");
    } else {
        const auto topology = agentTopology(copy.value, false);
        ENTITY_LIST selected;
        for (int index : agentIndices(params, "edges", topology.size())) selected.add(topology[index]);
        if (action == "component.fillet")
            agentCheck(api_blend_edges(selected, agentPositive(params, "radius")), "fillet edges");
        else if (action == "component.chamfer") {
            const double distance = agentPositive(params, "distance");
            const double distance2 = params.contains("distance2") ? agentPositive(params, "distance2") : distance;
            agentCheck(api_chamfer_edges(selected, distance, distance2), "chamfer edges");
        } else throw CommandError("unsupported advanced feature");
    }
    const json checked = agentSummary(copy.value, target->sid);
    if (!checked["valid"].get<bool>() || checked["solids"].get<int>() < 1)
        throw CommandError("feature did not preserve valid solid geometry");
    std::unordered_map<const void*, std::string> previous;
    collectPrevClosures({target}, previous);
    ComponentRef candidate{target->sid, copy.value, target->kind};
    result["modified"].push_back(json::parse(emitComponent(candidate, false, &previous)));
    delEntity(target->entity); target->entity = copy.release();
    return result.dump();
}
