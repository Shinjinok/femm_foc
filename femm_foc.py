import femm
import os
import matplotlib.pyplot as plt
import shutil
import subprocess
import time
from multiprocessing import Pool
import traceback
from PIL import Image, ImageDraw, ImageFont


def to_wine_path(path):
    """Convert a Linux absolute path to Wine Z: drive notation."""
    path = os.path.abspath(path)
    if path.startswith("/"):
        return "Z:" + path.replace("/", "\\")
    return path


def save_results_plot(results, output_path):
    """Save a chart of case torque results."""
    if not results:
        return

    labels = [f"Case {r['case_id']}" for r in results]
    torques = [r['torque'] if r['torque'] is not None else 0.0 for r in results]
    colors = ['tab:blue' if r['torque'] is not None else 'tab:red' for r in results]

    fig, ax = plt.subplots(figsize=(8, 5))
    bars = ax.bar(labels, torques, color=colors)

    ax.set_title('FEMM Case Torque Results')
    ax.set_ylabel('Torque (N·m)')
    ax.set_xlabel('Case')
    ax.grid(axis='y', linestyle='--', alpha=0.4)

    for rect, res in zip(bars, results):
        height = rect.get_height()
        label = f"{height:.2f}" if res['torque'] is not None else 'Fail'
        ax.annotate(
            label,
            xy=(rect.get_x() + rect.get_width() / 2, height),
            xytext=(0, 3),
            textcoords='offset points',
            ha='center',
            va='bottom',
            fontsize=9,
        )

    current_text = '\n'.join(
        [f"Case {r['case_id']}: A={r['I_a']} B={r['I_b']} C={r['I_c']} / {r['mech_angle_step']}°" for r in results]
    )
    fig.text(0.99, 0.01, current_text, ha='right', va='bottom', fontsize=8)
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)


def run_femm_case(args):
    """FEMM 해석을 다중 프로세스로 실행하고 토크를 반환합니다."""
    case_id, currents, mech_angle_step, fem_file, base_femm_dir, work_dir = args
    
    # 각 프로세스마다 독립적인 FEMM 디렉토리 생성
    femm_instance_dir = os.path.join(work_dir, f'femm_instance_{case_id}')
    shutil.rmtree(femm_instance_dir, ignore_errors=True)
    shutil.copytree(base_femm_dir, femm_instance_dir)
    shutil.copyfile(os.path.join(femm_instance_dir, 'femm.tlb'), os.path.join(femm_instance_dir, 'femm.TLB'))
    
    original_cwd = os.getcwd()
    wine_fem_file = to_wine_path(fem_file)
    
    temp_fem = os.path.join(work_dir, f"temp_step_{case_id}.fem")
    temp_ans = os.path.join(work_dir, f"temp_step_{case_id}.ans")
    
    I_a, I_b, I_c = currents
    torque = None
    
    try:
        os.chdir(femm_instance_dir)
        femm.openfemm(femmpath=femm_instance_dir)
        femm.opendocument(wine_fem_file)
        
        femm.mi_modifycircprop("A", 1, I_a)
        femm.mi_modifycircprop("B", 1, I_b)
        femm.mi_modifycircprop("C", 1, I_c)
        
        femm.mi_selectgroup(2)
        femm.mi_moverotate(0, 0, mech_angle_step)
        femm.mi_clearselected()
        
        femm.mi_saveas(temp_fem)
        femm.mi_analyze(1)
        femm.mi_loadsolution()
        
        # Show flux density plot
        #femm.mo_showdensityplot(1, 0, 1, 0.5, 1.5)
        
        # 토크 계산
        try:
            femm.mo_groupselectblock(2)
            torque = femm.mo_blockintegral(22)
            femm.mo_clearblock()
            if torque is None or torque == 0.0:
                print(f"Case {case_id} 경고: 토크 값이 0 또는 None입니다")
        except Exception as torque_err:
            print(f"Case {case_id} 토크 계산 에러: {torque_err}")
            traceback.print_exc()
            # 토크 계산 재시도
            try:
                femm.mo_clearblock()
                femm.mo_selectblock(2)
                torque = femm.mo_blockintegral(22)
                femm.mo_clearblock()
                print(f"Case {case_id} 재시도 성공, 토크: {torque}")
            except Exception as retry_err:
                print(f"Case {case_id} 토크 계산 재시도 실패: {retry_err}")
                torque = None
        
    except Exception as e:
        print(f"Case {case_id} 에러 발생: {e}")
        traceback.print_exc()
        torque = None
    
    finally:
        try:
            # Save plot before closing
            plot_file = os.path.join(work_dir, f"case_{case_id}_plot.png")
            wine_plot_file = to_wine_path(plot_file)
            femm.mo_savebitmap(wine_plot_file)
            
            # 이미지의 가로축 왼쪽 절반만 남기고 텍스트 추가
            try:
                img = Image.open(plot_file)
                width, height = img.size
                # 왼쪽 절반을 유지하기 (left, top, right, bottom)
                crop_box = (0, 0, width // 2, height)
                cropped_img = img.crop(crop_box)
                
                # 회전각도와 토크값을 텍스트로 추가
                draw = ImageDraw.Draw(cropped_img)
                try:
                    # 시스템 폰트 사용 시도
                    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 20)
                except:
                    # 폰트를 찾을 수 없으면 기본 폰트 사용
                    font = ImageFont.load_default()
                
                # 텍스트 작성
                text = f"Angle: {mech_angle_step}° "
                if torque is not None:
                    text += f"Torque: {torque:.4f} Nm"
                else:
                    text += "Torque: Failed"
                
                # 텍스트를 이미지 왼쪽 상단에 추가 (흰색 배경에 검은색 텍스트)
                text_bbox = draw.textbbox((0, 0), text, font=font)
                text_width = text_bbox[2] - text_bbox[0]
                text_height = text_bbox[3] - text_bbox[1]
                
                # 배경 사각형 그리기
                padding = 5
                draw.rectangle(
                    [(0, 0), (text_width + padding*2, text_height + padding*2)],
                    fill=(255, 255, 255)
                )
                # 텍스트 그리기
                draw.text((padding, padding), text, fill=(0, 0, 0), font=font)
                
                cropped_img.save(plot_file)
                print(f"플롯 저장, 크롭 및 텍스트 추가: {plot_file}")
            except Exception as crop_err:
                print(f"이미지 처리 실패: {crop_err}")
        except Exception as e:
            print(f"플롯 저장 실패: {e}")
        
        try:
            femm.closefemm()
        except Exception:
            pass
        
        os.chdir(original_cwd)
        
        if os.path.exists(temp_fem):
            os.remove(temp_fem)
        if os.path.exists(temp_ans):
            os.remove(temp_ans)
    
    return {
        "case_id": case_id,
        "I_a": I_a,
        "I_b": I_b,
        "I_c": I_c,
        "mech_angle_step": mech_angle_step,
        "torque": torque,
    }


def main():
    current_dir = os.getcwd()
    file_name = "lrk.fem"
    file_path = os.path.join(current_dir, file_name)

    base_femm_exe = os.path.expanduser("~/.wine/drive_c/femm42/bin/femm.exe")
    if not os.path.exists(base_femm_exe):
        raise FileNotFoundError(f"FEMM 실행 파일을 찾을 수 없습니다: {base_femm_exe}")

    base_femm_dir = os.path.dirname(base_femm_exe)

    cases = [
        # (case_num, (Ia, Ib, Ic), angle_deg)
        (1, (10.0, -5.0, -5.0), 0.0),
        (2, (9.986, -4.54, -5.446), 3.0),
        (3, (9.945, -4.067, -5.878), 6.0),
        (4, (9.877, -3.584, -6.293), 9.0),
        (5, (9.781, -3.09, -6.691), 12.0),
        (6, (9.659, -2.588, -7.071), 15.0),
        (7, (9.511, -2.079, -7.431), 18.0),
        (8, (9.336, -1.564, -7.771), 21.0),
        (9, (9.135, -1.045, -8.09), 24.0),
    ]

    # 멀티코어 프로세싱 준비
    num_cores = os.cpu_count()
    print(f"멀티코어 실행: {num_cores}개 프로세스로 {len(cases)}개 케이스 처리")
    
    # 각 케이스에 대한 인자 준비
    case_args = [(case_id, currents, mech_angle_step, file_path, base_femm_dir, current_dir) 
                 for case_id, currents, mech_angle_step in cases]
    
    # 멀티프로세싱 Pool로 병렬 실행
    with Pool(processes=num_cores) as pool:
        results = pool.map(run_femm_case, case_args)
    
    # 결과 출력
    for result in results:
        print(f"--- 케이스 {result['case_id']} 결과 ---")
        print(f"  인가 전류: A={result['I_a']}A, B={result['I_b']}A, C={result['I_c']}A")
        print(f"  회전 이동: {result['mech_angle_step']} 도")
        print(f"  발생 토크: {result['torque']:.4f} N.m" if result['torque'] is not None else "  토크 계산 실패")
    
    

if __name__ == "__main__":
    main()
