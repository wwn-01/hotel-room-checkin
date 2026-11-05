# 酒店房态管理简易Demo
# 状态：空闲、已清扫、查房中、已入住
rooms = {
    "101": {"status": "空闲"},
    "102": {"status": "已清扫"},
    "103": {"status": "查房中"},
    "104": {"status": "已入住"}
}

def show_rooms():
    print("====房间状态列表====")
    for room_num, info in rooms.items():
        print(f"房间{room_num}：{info['status']}")

def change_room_status(room_num, new_status):
    if room_num in rooms:
        rooms[room_num]["status"] = new_status
        print("✅ 修改成功！")
    else:
        print("❌ 该房间不存在")

# 测试
if __name__ == "__main__":
    show_rooms()
    print("\n把102房间改成空闲")
    change_room_status("102", "空闲")
    show_rooms()
